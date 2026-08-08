"""Browser-side Sentry support.

The Python SDK only ever sees the server half of a page.  This module builds
the snippet that boots Sentry's JavaScript SDK in the visitor's browser, using
a DSN of its own so that browser noise lands in a separate Sentry project
instead of drowning the backend one.

Nothing here is baked into a long-lived object: the snippet is rebuilt from
the settings for every page, so unlike the connection settings of the Python
client, every frontend setting takes effect without restarting Indico.

Like the Sentry hooks, the functions in here run on the request path and must
never raise - a broken frontend setting may cost us browser errors, but it may
not cost us the page.
"""

import logging
from urllib.parse import urlsplit

import sentry_sdk
from flask import g, has_request_context, request, session

import indico
from indico.core.config import config

from indico_sentry.hooks import SETTINGS
from indico_sentry.util import dsn_host, dsn_origin


logger = logging.getLogger('indico.plugin.sentry')

#: Version of the Sentry JavaScript SDK loaded from Sentry's CDN.  The
#: subresource integrity hashes below are the ones Sentry publishes for this
#: exact version, so the two are always bumped together.
SDK_VERSION = '10.69.0'

SDK_BASE_URL = 'https://browser.sentry-cdn.com'

#: sha384 checksums of the CDN bundles of :data:`SDK_VERSION`, taken from
#: Sentry's release registry.  Loading the smallest bundle that covers what is
#: actually enabled saves visitors a lot of JavaScript: the plain bundle is
#: ~90 kB, the one with tracing and replay ~270 kB.
SDK_INTEGRITY = {
    'bundle.min.js':
        'sha384-3CEt/dsT99DjKC3MgiUAiordZm0hoZjYMn6ioBvRKm+9A98CLWAUsQsk5XaPpjfU',
    'bundle.tracing.min.js':
        'sha384-Xl/pZ2YgviohWlNZ2ipnBKfonCQ2EKaXzusUClBh8mrgAiDZa3outR+4H52ZSOz9',
    'bundle.replay.min.js':
        'sha384-1qW4DnZb4gw678+NKCnQkWGoqSrdinRJjVuqwa4m0t0rDk3d9fQwRC+ZJyl6jaVR',
    'bundle.tracing.replay.min.js':
        'sha384-MD8yGlzUJGxSGvGu8Myu4qJfrBXaCtR6cYrkEFJvv/7u+dX4peuDFEi45ccaQABK',
}

#: How Session Replay records pages.  Sentry's own defaults mask text and
#: block media as well; setting them explicitly means an SDK upgrade cannot
#: quietly start recording what people type into a registration form.
REPLAY_OPTIONS = {'maskAllText': True, 'blockAllMedia': True}


def is_enabled(settings):
    """Whether the browser SDK should be injected at all."""
    return bool(settings.get('frontend_enabled') and _clean(settings.get('frontend_dsn')))


def frontend_context(settings=None):
    """Build everything the ``html-head`` snippet needs.

    Returns ``None`` when nothing should be injected, which is also what
    happens if anything at all goes wrong.
    """
    try:
        if settings is None:
            settings = SETTINGS.get()
        if not is_enabled(settings):
            return None
        if has_request_context() and g.get('static_site'):
            # Offline copies of an event are meant to work without a network;
            # they must not phone home to Sentry.
            return None
        url, integrity = sdk_script(settings)
        return {
            'sdk_url': url,
            'sdk_integrity': integrity,
            'options': init_options(settings),
            'user': user_info(settings),
            'tags': request_tags(),
            'replay': dict(REPLAY_OPTIONS),
            'trace': trace_meta(settings),
        }
    except Exception:
        logger.debug('Could not build the Sentry frontend context', exc_info=True)
        return None


def init_options(settings):
    """Build the object handed to ``Sentry.init()`` in the browser.

    Keys are the JavaScript SDK's own (camel-cased) option names.
    """
    options = {
        'dsn': _clean(settings.get('frontend_dsn')),
        'environment': _environment(settings),
        'release': _release(settings),
        # The privacy settings are shared with the backend: an admin who does
        # not want personal data in Sentry means both halves of the page.
        'sendDefaultPii': bool(settings.get('send_default_pii', True)),
        'maxBreadcrumbs': int(settings.get('max_breadcrumbs') or 100),
        'tracesSampleRate': _rate(settings.get('frontend_traces_sample_rate')),
        'replaysSessionSampleRate': _rate(settings.get('frontend_replay_session_sample_rate')),
        'replaysOnErrorSampleRate': _rate(settings.get('frontend_replay_error_sample_rate')),
    }
    if ignored := _lines(settings.get('frontend_ignored_errors')):
        options['ignoreErrors'] = ignored
    if denied := _lines(settings.get('frontend_denied_urls')):
        options['denyUrls'] = denied
    if targets := _lines(settings.get('frontend_trace_propagation_targets')):
        options['tracePropagationTargets'] = targets
    return options


def sdk_script(settings):
    """Return the ``(url, integrity)`` of the JavaScript SDK to load."""
    if url := _clean(settings.get('frontend_sdk_url')):
        # Self-hosted or pinned by the admin; we have no checksum for it.
        return url, None
    bundle = _bundle_name(settings)
    return f'{SDK_BASE_URL}/{SDK_VERSION}/{bundle}', SDK_INTEGRITY[bundle]


def script_origin(settings):
    """Origin the SDK is loaded from, for the ``script-src`` CSP directive."""
    try:
        url = urlsplit(sdk_script(settings)[0])
    except Exception:
        return None
    if not url.scheme or not url.netloc:
        return None
    return f'{url.scheme}://{url.netloc}'


def csp_script_source():
    """``script-src`` value needed by the browser SDK, or ``None``.

    Called from the ``get_csp_script_sources`` signal for every response, so
    it has to be cheap and silent.
    """
    try:
        settings = SETTINGS.get()
        if not is_enabled(settings):
            return None
        return script_origin(settings)
    except Exception:
        logger.debug('Could not determine the Sentry CSP script source', exc_info=True)
        return None


def user_info(settings):
    """The logged-in user, subject to the plugin's privacy settings."""
    if not settings.get('send_default_pii', True) or not has_request_context():
        return None
    try:
        if (user := session.user) is None:
            return None
        # The numeric id alone is enough to count affected users per issue.
        info = {'id': str(user.id)}
        if settings.get('send_user_email', True):
            info['email'] = user.email
            info['username'] = user.full_name
    except Exception:
        logger.debug('Could not add the user to the Sentry frontend context', exc_info=True)
        return None
    return {k: v for k, v in info.items() if v}


def request_tags():
    """Tags describing the page the SDK is being booted on.

    Only values that are already in the request are used - a template hook is
    not the place to start loading events from the database.
    """
    tags = {}
    if not has_request_context():
        return tags
    if endpoint := request.endpoint:
        tags['indico.endpoint'] = endpoint
    if event_id := (request.view_args or {}).get('event_id'):
        tags['indico.event_id'] = str(event_id)
    return tags


def trace_meta(settings):
    """Meta tags that tie the browser's pageload to the backend transaction.

    The JavaScript SDK picks up ``sentry-trace`` and ``baggage`` from the
    document, which is how a frontend trace and a backend trace end up as one
    trace even though they go to different projects.  We only emit them when
    the backend actually sampled this request: an unsampled parent would force
    the browser to drop its own trace too.
    """
    if not settings.get('frontend_link_traces', True):
        return None
    try:
        span = sentry_sdk.get_current_span()
        if span is None or not getattr(span, 'sampled', None):
            return None
        if not (traceparent := sentry_sdk.get_traceparent()):
            return None
        return {'sentry_trace': traceparent, 'baggage': sentry_sdk.get_baggage() or ''}
    except Exception:
        logger.debug('Could not build the Sentry trace meta tags', exc_info=True)
        return None


def frontend_summary(settings):
    """What the plugin settings page shows about the browser SDK."""
    options = init_options(settings)
    return {
        'enabled': is_enabled(settings),
        'requested': bool(settings.get('frontend_enabled')),
        'dsn_host': dsn_host(settings.get('frontend_dsn')),
        'environment': options['environment'],
        'release': options['release'],
        'sdk_url': sdk_script(settings)[0],
        'sdk_version': None if _clean(settings.get('frontend_sdk_url')) else SDK_VERSION,
        'traces_sample_rate': options['tracesSampleRate'],
        'replay': bool(options['replaysSessionSampleRate'] or options['replaysOnErrorSampleRate']),
        # Indico only lets plugins extend `script-src`; the rest has to be put
        # into CSP_DIRECTIVES by hand, so show admins what to add.
        'csp_script_src': script_origin(settings),
        'csp_connect_src': dsn_origin(settings.get('frontend_dsn')),
    }


def _bundle_name(settings):
    tracing = _rate(settings.get('frontend_traces_sample_rate')) > 0
    replay = bool(_rate(settings.get('frontend_replay_session_sample_rate'))
                  or _rate(settings.get('frontend_replay_error_sample_rate')))
    if tracing and replay:
        return 'bundle.tracing.replay.min.js'
    elif replay:
        return 'bundle.replay.min.js'
    elif tracing:
        return 'bundle.tracing.min.js'
    return 'bundle.min.js'


def _environment(settings):
    for value in (settings.get('frontend_environment'), settings.get('environment')):
        if cleaned := _clean(value):
            return cleaned
    try:
        return config.SENTRY_ENVIRONMENT
    except Exception:
        return None


def _release(settings):
    for value in (settings.get('frontend_release'), settings.get('release')):
        if cleaned := _clean(value):
            return cleaned
    return indico.__version__


def _clean(value):
    return value.strip() if isinstance(value, str) else ''


def _lines(values):
    if not isinstance(values, (list, tuple, set)):
        return []
    return [line.strip() for line in values if isinstance(line, str) and line.strip()]


def _rate(value):
    try:
        rate = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(max(rate, 0.0), 1.0)

"""Creation of the Sentry client.

Indico core already calls ``sentry_sdk.init()`` from
``indico.core.sentry.init_sentry`` before plugins are loaded, using only the
``SENTRY_*`` values from ``indico.conf``.  This module replaces that client
with one built from the plugin settings, adding the integrations and hooks
core does not set up.

Calling ``sentry_sdk.init()`` a second time is supported by the SDK: it binds
a new client to the global scope.  The previous client is closed explicitly so
its transport thread does not leak.
"""

import logging
from importlib.metadata import entry_points

import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

import indico
from indico.core.config import config

from indico_sentry import __version__ as plugin_version
from indico_sentry.hooks import SETTINGS, before_send, before_send_transaction, profiles_sampler, traces_sampler
from indico_sentry.util import INIT_ONLY_SETTINGS, dsn_host


logger = logging.getLogger('indico.plugin.sentry')

#: Snapshot of what the last ``init_sentry()`` call did, shown on the plugin
#: settings page so admins can tell what is actually running in this process.
status = {
    'initialized': False,
    'dsn_configured': False,
    'dsn_source': None,
    'dsn_host': None,
    'environment': None,
    'release': None,
    'integrations': [],
    'error': None,
    'plugin_version': plugin_version,
    #: The init-only settings this process was started with, so the settings
    #: page can point out changes that need a restart.
    'snapshot': {},
}


def init_sentry(settings, **extra_options):
    """(Re-)create the Sentry client from the given plugin settings.

    ``extra_options`` are passed straight to ``sentry_sdk.init()``; the tests
    use it to inject a fake transport.
    """
    status.update(initialized=False, error=None)
    try:
        options = build_options(settings, **extra_options)
    except Exception as exc:
        status['error'] = f'Could not build Sentry options: {exc}'
        logger.exception('Could not build Sentry options')
        return False

    old_client = sentry_sdk.get_client()
    ignore_logger('indico.flask')
    try:
        sentry_sdk.init(**options)
    except Exception as exc:
        status['error'] = f'sentry_sdk.init() failed: {exc}'
        logger.exception('sentry_sdk.init() failed')
        return False

    # Close the client core created so we do not leak its transport thread.
    try:
        if old_client is not sentry_sdk.get_client() and old_client.is_active():
            old_client.close(timeout=2.0)
    except Exception:
        logger.debug('Could not close the previous Sentry client', exc_info=True)

    # The hooks read their settings from the cache, so make sure they use the
    # same values the client was just built from.
    SETTINGS.prime(settings)
    status.update(
        initialized=True,
        dsn_configured=bool(options['dsn']),
        dsn_source=_resolve_dsn(settings)[1],
        environment=options['environment'],
        release=options['release'],
        integrations=sorted(type(i).__name__ for i in options['integrations']),
        dsn_host=dsn_host(options['dsn']),
        snapshot=snapshot_init_settings(settings),
    )
    if options['dsn']:
        logger.info('Sentry initialized (environment=%s, release=%s)', options['environment'], options['release'])
    else:
        logger.info('Sentry plugin loaded but no DSN is configured; nothing will be sent')
    return True


def reinit_sentry():
    """Re-create the client from the current settings, bypassing the cache."""
    SETTINGS.invalidate()
    return init_sentry(SETTINGS.get())


def is_sending():
    """Whether the current client would actually deliver events.

    ``Client.is_active()`` is true for any real client, even one created
    without a DSN - such a client has no transport and silently discards
    everything, which is exactly the case admins need to be warned about.
    """
    client = sentry_sdk.get_client()
    return bool(client.is_active() and getattr(client, 'transport', None) is not None)


def snapshot_init_settings(settings):
    """Keep the settings that can only be applied by re-creating the client."""
    return {key: settings.get(key) for key in sorted(INIT_ONLY_SETTINGS)}


def pending_restart_settings(saved_settings):
    """Return the init-only settings that changed since this worker started."""
    if not status['initialized']:
        return []
    snapshot = status['snapshot']
    current = snapshot_init_settings(saved_settings)
    return [key for key, value in current.items() if snapshot.get(key) != value]


def build_options(settings, **extra_options):
    """Build the kwargs for ``sentry_sdk.init()``."""
    dsn = _resolve_dsn(settings)[0]
    options = {
        'dsn': dsn,
        'environment': settings.get('environment') or config.SENTRY_ENVIRONMENT,
        'release': settings.get('release') or f'{indico.__version__}',
        'send_default_pii': bool(settings.get('send_default_pii', True)),
        'attach_stacktrace': bool(settings.get('attach_stacktrace', True)),
        'max_breadcrumbs': int(settings.get('max_breadcrumbs') or 100),
        # Indico is not part of a distributed trace by default; propagating
        # would attach our headers to every outgoing request.
        'propagate_traces': False,
        'in_app_include': sorted(_in_app_packages()),
        'integrations': _build_integrations(settings),
        'before_send': before_send,
        'before_send_transaction': before_send_transaction,
        # Samplers (rather than plain rates) let the sample rates be changed
        # from the settings page without restarting Indico.
        'traces_sampler': traces_sampler,
        'profiles_sampler': profiles_sampler,
        '_experiments': {'record_sql_params': True},
    }
    if server_name := settings.get('server_name'):
        options['server_name'] = server_name
    if disabled := _disabled_integrations(settings):
        options['disabled_integrations'] = disabled
    options.update(extra_options)
    return _drop_unsupported(options)


def _resolve_dsn(settings):
    if dsn := (settings.get('dsn') or '').strip():
        return dsn, 'plugin'
    if dsn := config.SENTRY_DSN:
        return dsn, 'indico.conf'
    return None, None


def _in_app_packages():
    """Treat indico and all installed plugins as application code."""
    packages = {'indico', 'indico_sentry'}
    try:
        packages |= {ep.value.split(':')[0].split('.')[0] for ep in entry_points(group='indico.plugins')}
    except Exception:
        logger.debug('Could not enumerate plugin packages', exc_info=True)
    return packages


def _build_integrations(settings):
    integrations = [
        # 'endpoint' groups transactions by Indico endpoint name (e.g.
        # ``events.display``), which is what the ignore/override settings
        # below are matched against.  Core uses 'url' instead.
        FlaskIntegration(transaction_style='endpoint'),
    ]

    if settings.get('enable_logging', True):
        level = getattr(logging, settings.get('logging_level') or 'WARNING', logging.WARNING)
        integrations.append(LoggingIntegration(level=logging.INFO, event_level=level))

    if settings.get('enable_celery', True):
        try:
            from sentry_sdk.integrations.celery import CeleryIntegration
            integrations.append(CeleryIntegration(monitor_beat_tasks=False, propagate_traces=False))
        except Exception:
            logger.warning('Celery integration is not available', exc_info=True)

    if settings.get('enable_sqlalchemy', False):
        try:
            from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
            integrations.append(SqlalchemyIntegration())
        except Exception:
            logger.warning('SQLAlchemy integration is not available', exc_info=True)

    if settings.get('enable_redis', True):
        try:
            from sentry_sdk.integrations.redis import RedisIntegration
            integrations.append(RedisIntegration())
        except Exception:
            logger.warning('Redis integration is not available', exc_info=True)

    try:
        from sentry_sdk.integrations.pure_eval import PureEvalIntegration
        integrations.append(PureEvalIntegration())
    except Exception:
        # pure_eval is an optional extra; stack traces just get less detail.
        logger.debug('pure_eval integration is not available', exc_info=True)

    return integrations


def _disabled_integrations(settings):
    """Turn off default integrations the admins opted out of."""
    if settings.get('enable_logging', True):
        return []
    return [LoggingIntegration]


def _drop_unsupported(options):
    """Remove options the installed sentry-sdk does not know about.

    Keeps the plugin working across SDK versions instead of failing at
    startup with ``Unknown option``.
    """
    try:
        from sentry_sdk.consts import DEFAULT_OPTIONS
    except ImportError:
        return options
    supported = set(DEFAULT_OPTIONS)
    unsupported = set(options) - supported
    for key in unsupported:
        logger.warning('Ignoring Sentry option %r which is not supported by sentry-sdk %s',
                       key, getattr(sentry_sdk, 'VERSION', '?'))
        del options[key]
    return options

"""Runtime hooks handed to the Sentry SDK.

Unlike the connection settings, everything in here is evaluated per event or
per transaction, which means changes made in the plugin settings take effect
without restarting Indico (after the short settings cache TTL).

None of these functions may raise: an exception inside ``before_send`` makes
the SDK silently drop the event, and an exception inside a sampler would
bubble up into the request.
"""

from urllib.parse import parse_qsl, urlencode

from indico_sentry.defaults import DEFAULT_SETTINGS
from indico_sentry.util import SettingsCache, exception_names, matches_any, parse_sample_overrides, scrub_mapping


SETTINGS = SettingsCache(DEFAULT_SETTINGS)


def before_send(event, hint):
    """Filter and scrub error events."""
    try:
        settings = SETTINGS.get()
        if _is_ignored(event, hint, settings):
            return None
        _apply_privacy(event, settings)
        _scrub(event, settings)
        return event
    except Exception:
        # Better to send an unfiltered event than to lose it entirely.
        return event


def before_send_transaction(event, hint):
    """Drop transactions for endpoints the admins are not interested in."""
    try:
        settings = SETTINGS.get()
        if matches_any(_transaction_name(event), settings.get('ignored_transactions')):
            return None
        _apply_privacy(event, settings)
        _scrub(event, settings)
        return event
    except Exception:
        return event


def traces_sampler(sampling_context):
    """Decide whether to trace a transaction, honouring per-endpoint rates."""
    return _sample(sampling_context, 'traces_sample_rate')


def profiles_sampler(sampling_context):
    """Decide whether to profile a sampled transaction."""
    return _sample(sampling_context, 'profiles_sample_rate')


def _sample(sampling_context, rate_key):
    try:
        settings = SETTINGS.get()
        name = _sampling_context_name(sampling_context)
        if matches_any(name, settings.get('ignored_transactions')):
            return 0.0
        overrides = parse_sample_overrides(settings.get('sample_rate_overrides'))
        for pattern, rate in overrides.items():
            if matches_any(name, [pattern]):
                return rate
        # Inherit the upstream decision for distributed traces.
        parent_sampled = sampling_context.get('parent_sampled')
        if parent_sampled is not None:
            return 1.0 if parent_sampled else 0.0
        return float(settings.get(rate_key) or 0.0)
    except Exception:
        return 0.0


def _sampling_context_name(sampling_context):
    transaction_context = sampling_context.get('transaction_context') or {}
    if name := transaction_context.get('name'):
        return name
    wsgi_environ = sampling_context.get('wsgi_environ') or {}
    return wsgi_environ.get('PATH_INFO', '')


def _transaction_name(event):
    if name := event.get('transaction'):
        return name
    return (event.get('extra') or {}).get('Endpoint') or ''


def _is_ignored(event, hint, settings):
    if matches_any(_transaction_name(event), settings.get('ignored_transactions')):
        return True

    logger_name = event.get('logger') or getattr(hint.get('log_record'), 'name', None)
    if matches_any(logger_name, settings.get('ignored_loggers')):
        return True

    ignored_exceptions = settings.get('ignored_exceptions')
    if ignored_exceptions and (exc_info := hint.get('exc_info')):
        exc = exc_info[1]
        if exc is not None and any(matches_any(name, ignored_exceptions) for name in exception_names(exc)):
            return True
    return False


def _apply_privacy(event, settings):
    if not settings.get('send_default_pii', True):
        event.pop('user', None)
        request = event.get('request') or {}
        request.pop('cookies', None)
        request.pop('data', None)
        headers = request.get('headers')
        if isinstance(headers, dict):
            for header in ('Cookie', 'Authorization', 'X-Forwarded-For', 'X-Real-Ip'):
                headers.pop(header, None)
        request.pop('env', None)
    else:
        if not settings.get('send_user_email', True) and isinstance(user := event.get('user'), dict):
            # Keep the user id so issues can still be counted per user.
            for key in ('email', 'username', 'ip_address', 'name'):
                user.pop(key, None)
        if not settings.get('send_request_body', True):
            (event.get('request') or {}).pop('data', None)


def _scrub(event, settings):
    scrub_fields = [f.lower() for f in settings.get('scrub_fields') or ()]
    if not scrub_fields:
        return
    for key in ('extra', 'contexts', 'tags'):
        if key in event:
            scrub_mapping(event[key], scrub_fields)
    if isinstance(request := event.get('request'), dict):
        for key in ('data', 'cookies', 'headers', 'env'):
            if key in request:
                scrub_mapping(request[key], scrub_fields)
        if isinstance(query_string := request.get('query_string'), str) and query_string:
            request['query_string'] = _scrub_query_string(query_string, scrub_fields)
    for entry in (event.get('breadcrumbs') or {}).get('values') or ():
        if isinstance(entry, dict) and 'data' in entry:
            scrub_mapping(entry['data'], scrub_fields)


def _scrub_query_string(query_string, scrub_fields):
    try:
        pairs = parse_qsl(query_string, keep_blank_values=True)
    except Exception:
        return query_string
    if not pairs:
        return query_string
    scrubbed = [(k, '[Filtered]' if any(f in k.lower() for f in scrub_fields) else v) for k, v in pairs]
    return urlencode(scrubbed)

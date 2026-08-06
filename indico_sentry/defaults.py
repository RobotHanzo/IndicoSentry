"""Default values for the Sentry plugin settings.

Kept in a module of its own so both the plugin and the Sentry hooks (which
must not import the plugin at import time) can use them.
"""

#: Keys whose values are replaced before an event leaves the server.  A key
#: matches if the fragment appears anywhere in its (lower-cased) name.
DEFAULT_SCRUB_FIELDS = [
    'password',
    'passwd',
    'secret',
    'token',
    'api_key',
    'apikey',
    'access_key',
    'private_key',
    'authorization',
    'session',
    'csrf',
    'credit_card',
    'card_number',
    'cvv',
    'iban',
]

#: Loggers that are noisy or that would cause feedback loops.
DEFAULT_IGNORED_LOGGERS = [
    'indico.flask',
    'werkzeug',
]

#: Exceptions that are part of normal operation and not worth an alert.
DEFAULT_IGNORED_EXCEPTIONS = [
    'werkzeug.exceptions.NotFound',
    'werkzeug.exceptions.Forbidden',
    'werkzeug.exceptions.Unauthorized',
    'werkzeug.exceptions.MethodNotAllowed',
    'werkzeug.exceptions.BadRequest',
    'indico.core.errors.NoReportError',
    'indico.core.errors.UserValueError',
]

#: Endpoints that generate a lot of traffic but very little insight.
DEFAULT_IGNORED_TRANSACTIONS = [
    'assets.*',
    'core.ping',
    'core.health_check',
]

DEFAULT_SETTINGS = {
    # --- connection (init-only) ---
    'dsn': '',
    'environment': '',
    'release': '',
    'server_name': '',
    # --- integrations (init-only) ---
    'enable_logging': True,
    'logging_level': 'WARNING',
    'enable_celery': True,
    'enable_sqlalchemy': False,
    'enable_redis': True,
    'attach_stacktrace': True,
    'max_breadcrumbs': 100,
    # --- performance (live) ---
    'traces_sample_rate': 0.0,
    'profiles_sample_rate': 0.0,
    'sample_rate_overrides': [],
    # --- filtering (live) ---
    'ignored_exceptions': DEFAULT_IGNORED_EXCEPTIONS,
    'ignored_loggers': DEFAULT_IGNORED_LOGGERS,
    'ignored_transactions': DEFAULT_IGNORED_TRANSACTIONS,
    # --- privacy (live) ---
    'send_default_pii': True,
    'send_user_email': True,
    'send_request_body': True,
    'scrub_fields': DEFAULT_SCRUB_FIELDS,
    # --- indico context (live) ---
    'event_context': True,
    'category_context': True,
}

#: Log levels offered for the logging integration.
LOGGING_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

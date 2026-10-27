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
    # WeasyPrint warns once per glyph the installed fonts lack, so a single PDF
    # with CJK names produced 127 events in 3 seconds.  Missing fonts are an
    # installation problem, not a code one.  fontTools, which it uses to embed
    # fonts, gets the same treatment.
    'weasyprint*',
    'fontTools*',
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

#: Browser errors that are noise rather than bugs: layout callbacks browsers
#: fire on themselves, the failures every page gets when a visitor loses
#: connectivity or navigates away mid-request, and errors from code that
#: browsers or apps inject into the page.  The SDK matches these as substrings.
DEFAULT_FRONTEND_IGNORED_ERRORS = [
    'ResizeObserver loop limit exceeded',
    'ResizeObserver loop completed with undelivered notifications',
    'Non-Error promise rejection captured',
    'NetworkError when attempting to fetch resource',
    'Failed to fetch',
    'Load failed',
    'AbortError',
    'Network Error',  # axios' wording, which is what all of Indico's own requests use
    'Java object is gone',  # Instagram/Facebook in-app browser on Android
    'window.webkit.messageHandlers',  # Instagram/Facebook in-app browser on iOS
    '"@context"].toLowerCase',  # Safari's JSON-LD reader tripping over a top-level array
]

#: Scripts that are not ours.  Browser extensions and in-app browsers inject
#: code into every page and their exceptions would otherwise show up as Indico
#: errors.
DEFAULT_FRONTEND_DENIED_URLS = [
    'chrome-extension://',
    'chrome://',
    'moz-extension://',
    'safari-extension://',
    'safari-web-extension://',
    'iabjs://',  # scripts injected by the Instagram/Facebook in-app browser
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
    # --- frontend / browser (live) ---
    # The browser SDK is configured from scratch on every page render, so none
    # of these need a restart.
    'frontend_enabled': False,
    'frontend_dsn': '',
    'frontend_environment': '',
    'frontend_release': '',
    'frontend_traces_sample_rate': 0.0,
    'frontend_replay_session_sample_rate': 0.0,
    'frontend_replay_error_sample_rate': 0.0,
    'frontend_trace_propagation_targets': [],
    'frontend_ignored_errors': DEFAULT_FRONTEND_IGNORED_ERRORS,
    'frontend_denied_urls': DEFAULT_FRONTEND_DENIED_URLS,
    'frontend_link_traces': True,
    'frontend_sdk_url': '',
}

#: Log levels offered for the logging integration.
LOGGING_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

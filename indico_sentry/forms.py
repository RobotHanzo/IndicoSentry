"""Settings form shown on the plugin's admin page."""

from wtforms.fields import BooleanField, FloatField, IntegerField, SelectField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional, ValidationError

from indico.web.forms.base import IndicoForm
from indico.web.forms.fields import IndicoPasswordField, TextListField
from indico.web.forms.validators import HiddenUnless
from indico.web.forms.widgets import SwitchWidget

from indico_sentry import _
from indico_sentry.defaults import LOGGING_LEVELS


def _frontend_only():
    """Hide a field while the browser SDK is off, without losing its value."""
    return HiddenUnless('frontend_enabled', preserve_data=True)


class SampleRateOverridesField(TextListField):
    """One ``endpoint = rate`` rule per line."""

    def _validate_item(self, line):
        name, sep, rate = line.partition('=')
        if not sep:
            raise ValidationError(_('Invalid rule (expected "endpoint = rate"): {}').format(line))
        if not name.strip():
            raise ValidationError(_('Missing endpoint name in: {}').format(line))
        try:
            value = float(rate.strip())
        except ValueError:
            raise ValidationError(_('"{}" is not a valid sample rate').format(rate.strip()))
        if not 0 <= value <= 1:
            raise ValidationError(_('Sample rate must be between 0 and 1, got {}').format(value))


class SettingsForm(IndicoForm):
    _fieldsets = [
        (_('Connection'), ('dsn', 'environment', 'release', 'server_name')),
        (_('Integrations'), ('enable_logging', 'logging_level', 'enable_celery', 'enable_sqlalchemy',
                             'enable_redis', 'attach_stacktrace', 'max_breadcrumbs')),
        (_('Performance'), ('traces_sample_rate', 'profiles_sample_rate', 'sample_rate_overrides')),
        (_('Filtering'), ('ignored_exceptions', 'ignored_loggers', 'ignored_transactions')),
        (_('Privacy'), ('send_default_pii', 'send_user_email', 'send_request_body', 'scrub_fields')),
        (_('Indico context'), ('event_context', 'category_context')),
        (_('Frontend (browser)'), ('frontend_enabled', 'frontend_dsn', 'frontend_environment', 'frontend_release',
                                   'frontend_traces_sample_rate', 'frontend_replay_session_sample_rate',
                                   'frontend_replay_error_sample_rate', 'frontend_link_traces',
                                   'frontend_trace_propagation_targets', 'frontend_ignored_errors',
                                   'frontend_denied_urls', 'frontend_sdk_url')),
    ]

    # --- Connection ---------------------------------------------------------
    dsn = IndicoPasswordField(_('Sentry DSN'), toggle=True,
                              description=_('The DSN of your Sentry project. Leave empty to fall back to the '
                                            'SENTRY_DSN set in indico.conf. Requires a restart to take effect.'))
    environment = StringField(_('Environment'),
                              description=_('Environment reported to Sentry (e.g. production, staging). Defaults to '
                                            'SENTRY_ENVIRONMENT from indico.conf. Requires a restart.'))
    release = StringField(_('Release'),
                          description=_('Release reported to Sentry. Defaults to the Indico version. '
                                        'Requires a restart.'))
    server_name = StringField(_('Server name'),
                              description=_('Overrides the hostname reported to Sentry. Requires a restart.'))

    # --- Integrations -------------------------------------------------------
    enable_logging = BooleanField(_('Capture log records'), widget=SwitchWidget(),
                                  description=_('Send log records to Sentry as events. Requires a restart.'))
    logging_level = SelectField(_('Minimum log level'), choices=[(level, level) for level in LOGGING_LEVELS],
                                validators=[HiddenUnless('enable_logging'), DataRequired()],
                                description=_('Log records at or above this level become Sentry events. '
                                              'Requires a restart.'))
    enable_celery = BooleanField(_('Celery integration'), widget=SwitchWidget(),
                                 description=_('Report errors from background tasks, with the task name as the '
                                               'transaction. Requires a restart.'))
    enable_sqlalchemy = BooleanField(_('SQLAlchemy integration'), widget=SwitchWidget(),
                                     description=_('Add a span for every SQL query. Very detailed, but noticeably '
                                                   'increases the size of traces. Requires a restart.'))
    enable_redis = BooleanField(_('Redis integration'), widget=SwitchWidget(),
                                description=_('Add breadcrumbs and spans for Redis commands. Requires a restart.'))
    attach_stacktrace = BooleanField(_('Attach stack traces'), widget=SwitchWidget(),
                                     description=_('Attach a stack trace to messages that have no exception. '
                                                   'Requires a restart.'))
    max_breadcrumbs = IntegerField(_('Max breadcrumbs'), [NumberRange(min=0, max=500)],
                                   description=_('How many breadcrumbs to keep per event. Requires a restart.'))

    # --- Performance --------------------------------------------------------
    traces_sample_rate = FloatField(_('Trace sample rate'), [Optional(), NumberRange(min=0, max=1)],
                                    description=_('Fraction of requests to trace, between 0 and 1. Start low (e.g. '
                                                  '0.01) on a busy instance. Applied without a restart.'))
    profiles_sample_rate = FloatField(_('Profile sample rate'), [Optional(), NumberRange(min=0, max=1)],
                                      description=_('Fraction of traced requests to also profile. Has no effect '
                                                    'unless tracing is enabled. Applied without a restart.'))
    sample_rate_overrides = SampleRateOverridesField(
        _('Per-endpoint sample rates'),
        description=_('One rule per line, e.g. "events.display = 0.5". A trailing * matches a prefix, '
                      'e.g. "assets.* = 0". Applied without a restart.'))

    # --- Filtering ----------------------------------------------------------
    ignored_exceptions = TextListField(_('Ignored exceptions'),
                                       description=_('One exception class per line, either a bare name or a dotted '
                                                     'path. Subclasses are matched too. Applied without a restart.'))
    ignored_loggers = TextListField(_('Ignored loggers'),
                                    description=_('One logger name per line; a trailing * matches a prefix. '
                                                  'Applied without a restart.'))
    ignored_transactions = TextListField(_('Ignored endpoints'),
                                         description=_('One Indico endpoint per line, e.g. "assets.*". Events and '
                                                       'traces for these endpoints are dropped. Applied without a '
                                                       'restart.'))

    # --- Privacy ------------------------------------------------------------
    send_default_pii = BooleanField(_('Send personal data'), widget=SwitchWidget(),
                                    description=_('Include the user, request headers, cookies and body in events. '
                                                  'Turn this off to keep personal data out of Sentry.'))
    send_user_email = BooleanField(_('Send user email and name'), widget=SwitchWidget(),
                                   validators=[HiddenUnless('send_default_pii')],
                                   description=_('Include the email, name and IP of the affected user. When off, '
                                                 'only the numeric user id is sent.'))
    send_request_body = BooleanField(_('Send request body'), widget=SwitchWidget(),
                                     validators=[HiddenUnless('send_default_pii')],
                                     description=_('Include submitted form data in events.'))
    scrub_fields = TextListField(_('Scrubbed fields'),
                                 description=_('One fragment per line. Any field whose name contains one of these is '
                                               'replaced with "[Filtered]". Applied without a restart.'))

    # --- Indico context -----------------------------------------------------
    event_context = BooleanField(_('Event context'), widget=SwitchWidget(),
                                 description=_('Tag events with the Indico event, contribution and registration '
                                               'being accessed.'))
    category_context = BooleanField(_('Category context'), widget=SwitchWidget(),
                                    description=_('Tag events with the category being accessed.'))

    # --- Frontend -----------------------------------------------------------
    # Everything below is hidden while the frontend is off, but keeps its data
    # so that turning the switch back on does not mean re-entering the DSN.
    frontend_enabled = BooleanField(_('Report browser errors'), widget=SwitchWidget(),
                                    description=_('Load the Sentry JavaScript SDK on every Indico page so that '
                                                  'errors happening in the browser are reported too. Needs a DSN '
                                                  'of its own below. Applied without a restart.'))
    frontend_dsn = IndicoPasswordField(_('Browser DSN'), [_frontend_only()], toggle=True,
                                       description=_('The DSN of the Sentry project that receives browser errors. '
                                                     'Use a separate project from the server one: it is public (it '
                                                     'ends up in the HTML of every page) and browser errors are far '
                                                     'noisier than server errors.'))
    frontend_environment = StringField(_('Browser environment'), [_frontend_only()],
                                       description=_('Environment reported by the browser SDK. Defaults to the '
                                                     'server-side environment above.'))
    frontend_release = StringField(_('Browser release'), [_frontend_only()],
                                   description=_('Release reported by the browser SDK. Defaults to the server-side '
                                                 'release above.'))
    frontend_traces_sample_rate = FloatField(_('Browser trace sample rate'),
                                             [_frontend_only(), Optional(), NumberRange(min=0, max=1)],
                                             description=_('Fraction of page loads to trace in the browser. Above 0 '
                                                           'this loads a larger SDK bundle.'))
    frontend_replay_session_sample_rate = FloatField(_('Session replay sample rate'),
                                                     [_frontend_only(), Optional(), NumberRange(min=0, max=1)],
                                                     description=_('Fraction of sessions to record as a replay. Text '
                                                                   'is masked and media blocked. Above 0 this loads a '
                                                                   'considerably larger SDK bundle.'))
    frontend_replay_error_sample_rate = FloatField(_('Replay on error sample rate'),
                                                   [_frontend_only(), Optional(), NumberRange(min=0, max=1)],
                                                   description=_('Fraction of sessions that hit an error to record '
                                                                 'as a replay, on top of the rate above.'))
    frontend_link_traces = BooleanField(_('Link browser and server traces'), [_frontend_only()],
                                        widget=SwitchWidget(),
                                        description=_('Add sentry-trace and baggage meta tags so a page load and the '
                                                      'request that produced it show up as one trace. Only has an '
                                                      'effect on requests the server actually traced.'))
    frontend_trace_propagation_targets = TextListField(
        _('Trace propagation targets'), [_frontend_only()],
        description=_('One URL or regex per line that browser requests may carry trace headers to. Leave empty to '
                      'use the SDK default, which is same-origin requests only.'))
    frontend_ignored_errors = TextListField(_('Ignored browser errors'), [_frontend_only()],
                                            description=_('One error message per line; a message containing the text '
                                                          'is dropped. The defaults cover the usual browser noise.'))
    frontend_denied_urls = TextListField(_('Denied script URLs'), [_frontend_only()],
                                         description=_('One URL fragment per line. Errors raised by scripts from '
                                                       'these URLs are dropped; the defaults keep browser extensions '
                                                       'out of your Sentry project.'))
    frontend_sdk_url = StringField(_('Custom SDK URL'), [_frontend_only()],
                                   description=_("Load the JavaScript SDK from here instead of Sentry's CDN, e.g. a "
                                                 'self-hosted copy. Leave empty unless you know you need it: the '
                                                 'bundled URL is version-pinned and checksum-verified.'))

    def validate_dsn(self, field):
        self._validate_dsn(field)

    def validate_frontend_dsn(self, field):
        self._validate_dsn(field)

    def _validate_dsn(self, field):
        if not field.data:
            return
        try:
            from sentry_sdk.utils import Dsn
            Dsn(field.data)
        except ImportError:
            pass
        except Exception:
            raise ValidationError(_('This does not look like a valid Sentry DSN.'))

    def validate_frontend_sdk_url(self, field):
        if not field.data:
            return
        if not field.data.strip().startswith(('https://', '/')):
            raise ValidationError(_('The SDK URL must be an https:// URL or an absolute path.'))

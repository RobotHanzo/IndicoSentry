"""The Indico Sentry plugin.

Indico core initialises Sentry from ``indico.conf`` before plugins are loaded.
This plugin replaces that client with one that can be configured from the
admin area and that knows about Celery, SQLAlchemy, Indico events and Indico
endpoints.  It also injects Sentry's JavaScript SDK into every page so that
browser errors are reported to a Sentry project of their own.
"""

import copy

import sentry_sdk

from indico.core import signals
from indico.core.db import db
from indico.core.plugins import IndicoPlugin, get_plugin_template_module, url_for_plugin

from indico_sentry.blueprint import blueprint
from indico_sentry.cli import cli
from indico_sentry.client import init_sentry, is_sending, pending_restart_settings, status
from indico_sentry.context import set_rh_context
from indico_sentry.defaults import DEFAULT_SETTINGS
from indico_sentry.forms import SettingsForm
from indico_sentry.frontend import csp_script_source, frontend_context, frontend_summary
from indico_sentry.hooks import SETTINGS


class SentryPlugin(IndicoPlugin):
    """Sentry

    Sends errors and performance data from Indico to Sentry.

    On top of the basic Sentry support built into Indico this adds Celery and
    SQLAlchemy integrations, Indico event/category context, performance
    tracing, error filtering and scrubbing rules that can be managed from here
    instead of ``indico.conf``, and browser-side error reporting through a
    Sentry project of its own.
    """

    configurable = True
    # NOTE: do not set `category = PluginCategory.other`.  RHPlugins builds the
    # category buckets and then does `ordered_categories[PluginCategory.other] =
    # <uncategorised plugins>`, which overwrites the bucket holding plugins that
    # explicitly declared that category - they disappear from the admin list.
    # Leaving `category` unset puts us in the uncategorised list, which is what
    # actually gets rendered under "Other".
    settings_form = SettingsForm
    default_settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings_form_field_opts = {
        'sample_rate_overrides': {'rows': 4},
        'ignored_exceptions': {'rows': 6},
        'ignored_loggers': {'rows': 4},
        'ignored_transactions': {'rows': 4},
        'scrub_fields': {'rows': 8},
        'frontend_trace_propagation_targets': {'rows': 3},
        'frontend_ignored_errors': {'rows': 6},
        'frontend_denied_urls': {'rows': 5},
    }

    def init(self):
        super().init()
        # Core calls sentry_sdk.init() before plugins are loaded, so we wait
        # for app_created (at which point the database is usable) and replace
        # that client with ours.
        self.connect(signals.core.app_created, self._app_created)
        self.connect(signals.rh.process_args, set_rh_context)
        self.connect(signals.plugin.cli, lambda sender, **kwargs: cli)
        # Indico only grew Content-Security-Policy support in 3.3.10; on older
        # versions there is no script-src for us to add the SDK host to.
        if csp_signal := getattr(signals.core, 'get_csp_script_sources', None):
            self.connect(csp_signal, self._get_csp_script_sources)
        self.template_hook('plugin-details', self._inject_details)
        self.template_hook('html-head', self._inject_frontend)

    def get_blueprints(self):
        return blueprint

    def _app_created(self, app, **kwargs):
        SETTINGS.invalidate()
        try:
            init_sentry(SETTINGS.get())
        finally:
            # Reading our settings is a database query, and core's check_db() runs
            # right after app_created: uwsgi forks its workers after make_app(), so
            # the pool must be empty by then.  Without this core logs a warning (which
            # we then send to Sentry) on every start, and refuses to start at all
            # when DEBUG is on.
            db.session.remove()
            db.engine.dispose()

    def _inject_details(self, plugin, **kwargs):
        # This hook fires for every plugin's details page.
        if plugin.name != self.name:
            return ''
        tpl = get_plugin_template_module('_details.html')
        return tpl.render_status(
            status=status,
            active=is_sending(),
            sdk_version=getattr(sentry_sdk, 'VERSION', '?'),
            pending=self._pending_restart_settings(),
            frontend=self._frontend_summary(),
            test_url=url_for_plugin('sentry.test'),
            reload_url=url_for_plugin('sentry.reload'),
        )

    def _inject_frontend(self, **kwargs):
        """Boot the browser SDK in the ``<head>`` of every Indico page."""
        try:
            if (context := frontend_context()) is None:
                return ''
            tpl = get_plugin_template_module('_frontend.html')
            return tpl.render_frontend(context)
        except Exception:
            # This runs on every single page; error reporting is never worth
            # breaking one over.
            self.logger.debug('Could not render the Sentry frontend snippet', exc_info=True)
            return ''

    def _get_csp_script_sources(self, sender, **kwargs):
        """Let the SDK be loaded when the instance runs with a strict CSP."""
        return csp_script_source()

    def _saved_settings(self):
        saved = dict(DEFAULT_SETTINGS)
        saved.update(self.settings.get_all())
        return saved

    def _pending_restart_settings(self):
        try:
            return pending_restart_settings(self._saved_settings())
        except Exception:
            self.logger.debug('Could not determine pending Sentry settings', exc_info=True)
            return []

    def _frontend_summary(self):
        try:
            return frontend_summary(self._saved_settings())
        except Exception:
            self.logger.debug('Could not summarise the Sentry frontend settings', exc_info=True)
            return None

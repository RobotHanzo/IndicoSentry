"""The Indico Sentry plugin.

Indico core initialises Sentry from ``indico.conf`` before plugins are loaded.
This plugin replaces that client with one that can be configured from the
admin area and that knows about Celery, SQLAlchemy, Indico events and Indico
endpoints.
"""

import copy

import sentry_sdk

from indico.core import signals
from indico.core.plugins import IndicoPlugin, PluginCategory, get_plugin_template_module, url_for_plugin

from indico_sentry.blueprint import blueprint
from indico_sentry.cli import cli
from indico_sentry.client import init_sentry, is_sending, pending_restart_settings, status
from indico_sentry.context import set_rh_context
from indico_sentry.defaults import DEFAULT_SETTINGS
from indico_sentry.forms import SettingsForm
from indico_sentry.hooks import SETTINGS


class SentryPlugin(IndicoPlugin):
    """Sentry

    Sends errors and performance data from Indico to Sentry.

    On top of the basic Sentry support built into Indico this adds Celery and
    SQLAlchemy integrations, Indico event/category context, performance
    tracing, and error filtering and scrubbing rules that can be managed from
    here instead of ``indico.conf``.
    """

    configurable = True
    category = PluginCategory.other
    settings_form = SettingsForm
    default_settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings_form_field_opts = {
        'sample_rate_overrides': {'rows': 4},
        'ignored_exceptions': {'rows': 6},
        'ignored_loggers': {'rows': 4},
        'ignored_transactions': {'rows': 4},
        'scrub_fields': {'rows': 8},
    }

    def init(self):
        super().init()
        # Core calls sentry_sdk.init() before plugins are loaded, so we wait
        # for app_created (at which point the database is usable) and replace
        # that client with ours.
        self.connect(signals.core.app_created, self._app_created)
        self.connect(signals.rh.process_args, set_rh_context)
        self.connect(signals.plugin.cli, lambda sender, **kwargs: cli)
        self.template_hook('plugin-details', self._inject_details)

    def get_blueprints(self):
        return blueprint

    def _app_created(self, app, **kwargs):
        SETTINGS.invalidate()
        init_sentry(SETTINGS.get())

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
            test_url=url_for_plugin('sentry.test'),
            reload_url=url_for_plugin('sentry.reload'),
        )

    def _pending_restart_settings(self):
        try:
            saved = dict(DEFAULT_SETTINGS)
            saved.update(self.settings.get_all())
            return pending_restart_settings(saved)
        except Exception:
            self.logger.debug('Could not determine pending Sentry settings', exc_info=True)
            return []

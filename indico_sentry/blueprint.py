from indico.core.plugins import IndicoPluginBlueprint

from indico_sentry.controllers import RHSentryReload, RHSentryTest


blueprint = IndicoPluginBlueprint('sentry', __name__, url_prefix='/admin/plugins/sentry')
blueprint.add_url_rule('/test', 'test', RHSentryTest, methods=('POST',))
blueprint.add_url_rule('/reload', 'reload', RHSentryReload, methods=('POST',))

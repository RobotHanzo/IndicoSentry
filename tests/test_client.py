"""Tests for building and installing the Sentry client.

These need an app context because the plugin falls back to the ``SENTRY_*``
values from ``indico.conf``.  They do not need a database.
"""

import logging

import sentry_sdk

from indico_sentry.client import build_options, init_sentry, is_sending, pending_restart_settings, status
from indico_sentry.defaults import DEFAULT_SETTINGS


DSN = 'https://abc123@o1.ingest.sentry.io/2'
OTHER_DSN = 'https://def456@o9.ingest.sentry.io/9'


def settings(**overrides):
    return dict(DEFAULT_SETTINGS, **overrides)


def integration_names(options):
    return {type(i).__name__ for i in options['integrations']}


class TestBuildOptions:
    def test_defaults(self, app_ctx):
        options = build_options(settings())
        assert options['environment'] == 'production'
        assert options['release']
        assert options['propagate_traces'] is False
        assert callable(options['before_send'])
        assert callable(options['before_send_transaction'])
        assert callable(options['traces_sampler'])

    def test_plugin_dsn_wins_over_config(self, make_app_ctx):
        make_app_ctx(SENTRY_DSN=OTHER_DSN)
        assert build_options(settings(dsn=DSN))['dsn'] == DSN

    def test_falls_back_to_indico_conf(self, make_app_ctx):
        make_app_ctx(SENTRY_DSN=OTHER_DSN)
        assert build_options(settings())['dsn'] == OTHER_DSN

    def test_falls_back_to_indico_conf_environment(self, make_app_ctx):
        make_app_ctx(SENTRY_ENVIRONMENT='staging')
        assert build_options(settings())['environment'] == 'staging'
        assert build_options(settings(environment='qa'))['environment'] == 'qa'

    def test_no_dsn_anywhere(self, app_ctx):
        assert build_options(settings())['dsn'] is None

    def test_indico_is_in_app(self, app_ctx):
        in_app = build_options(settings())['in_app_include']
        assert 'indico' in in_app
        assert 'indico_sentry' in in_app

    def test_integration_toggles(self, app_ctx):
        assert 'CeleryIntegration' in integration_names(build_options(settings(enable_celery=True)))
        assert 'CeleryIntegration' not in integration_names(build_options(settings(enable_celery=False)))
        assert 'SqlalchemyIntegration' in integration_names(build_options(settings(enable_sqlalchemy=True)))
        assert 'SqlalchemyIntegration' not in integration_names(build_options(settings(enable_sqlalchemy=False)))
        assert 'RedisIntegration' not in integration_names(build_options(settings(enable_redis=False)))

    def test_flask_integration_uses_endpoint_names(self, app_ctx):
        flask_integration = next(i for i in build_options(settings())['integrations']
                                 if type(i).__name__ == 'FlaskIntegration')
        assert flask_integration.transaction_style == 'endpoint'

    def test_logging_integration_can_be_disabled(self, app_ctx):
        options = build_options(settings(enable_logging=False))
        assert 'LoggingIntegration' not in integration_names(options)
        assert options['disabled_integrations']

    def test_logging_level_is_applied(self, app_ctx):
        integration = next(i for i in build_options(settings(logging_level='ERROR'))['integrations']
                           if type(i).__name__ == 'LoggingIntegration')
        assert integration._handler.level == logging.ERROR

    def test_server_name_only_when_set(self, app_ctx):
        assert 'server_name' not in build_options(settings())
        assert build_options(settings(server_name='web-1'))['server_name'] == 'web-1'

    def test_extra_options_are_passed_through(self, app_ctx, transport):
        assert build_options(settings(), transport=transport)['transport'] is transport

    def test_unsupported_options_are_dropped(self, app_ctx):
        assert 'definitely_not_a_sentry_option' not in build_options(settings(),
                                                                    definitely_not_a_sentry_option=1)

    def test_only_known_options_survive(self, app_ctx):
        from sentry_sdk.consts import DEFAULT_OPTIONS
        assert not set(build_options(settings(dsn=DSN))) - set(DEFAULT_OPTIONS)


class TestInitSentry:
    def test_activates_client(self, app_ctx, transport):
        assert init_sentry(settings(dsn=DSN), transport=transport)
        assert is_sending()
        assert status['initialized']
        assert status['dsn_configured']
        assert status['dsn_host'] == 'o1.ingest.sentry.io'
        assert status['dsn_source'] == 'plugin'
        assert 'FlaskIntegration' in status['integrations']

    def test_reports_config_as_dsn_source(self, make_app_ctx, transport):
        make_app_ctx(SENTRY_DSN=OTHER_DSN)
        init_sentry(settings(), transport=transport)
        assert status['dsn_source'] == 'indico.conf'

    def test_without_dsn_nothing_is_sent(self, app_ctx, transport):
        # A client without a DSN has no transport and silently drops events,
        # which `is_active()` alone does not tell you.
        assert init_sentry(settings())
        assert status['initialized']
        assert not status['dsn_configured']
        assert not is_sending()

    def test_events_go_through_before_send(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        sentry_sdk.capture_event({
            'message': 'hello',
            'request': {'data': {'password': 'hunter2', 'title': 'ok'}},
        })
        assert len(transport.events) == 1
        assert transport.events[0]['request']['data']['password'] == '[Filtered]'
        assert transport.events[0]['request']['data']['title'] == 'ok'

    def test_ignored_events_are_not_sent(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN, ignored_transactions=['assets.*']), transport=transport)
        sentry_sdk.capture_event({'message': 'nope', 'transaction': 'assets.js'})
        assert transport.events == []

    def test_ignored_exceptions_are_not_sent(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN, ignored_exceptions=['ValueError']), transport=transport)
        try:
            raise ValueError('expected')
        except ValueError:
            sentry_sdk.capture_exception()
        assert transport.events == []

    def test_unignored_exceptions_are_sent(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN, ignored_exceptions=['ValueError']), transport=transport)
        try:
            raise KeyError('expected')
        except KeyError:
            sentry_sdk.capture_exception()
        assert len(transport.events) == 1

    def test_hooks_use_the_settings_the_client_was_built_from(self, app_ctx, transport):
        from indico_sentry.hooks import SETTINGS
        applied = settings(dsn=DSN, scrub_fields=['nickname'])
        init_sentry(applied, transport=transport)
        assert SETTINGS.get()['scrub_fields'] == ['nickname']

    def test_survives_a_broken_dsn(self, app_ctx, transport):
        assert not init_sentry(settings(dsn='not-a-dsn'))
        assert not status['initialized']
        assert status['error']


class TestPendingRestartSettings:
    def test_none_pending_right_after_init(self, app_ctx, transport):
        current = settings(dsn=DSN)
        init_sentry(current, transport=transport)
        assert pending_restart_settings(current) == []

    def test_detects_changed_dsn(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        assert pending_restart_settings(settings(dsn=OTHER_DSN)) == ['dsn']

    def test_detects_changed_integration_toggle(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        assert pending_restart_settings(settings(dsn=DSN, enable_celery=False)) == ['enable_celery']

    def test_ignores_live_settings(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        changed = settings(dsn=DSN, traces_sample_rate=0.5, scrub_fields=['x'], ignored_loggers=[])
        assert pending_restart_settings(changed) == []

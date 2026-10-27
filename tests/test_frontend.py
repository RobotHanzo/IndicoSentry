"""Tests for the browser-side Sentry snippet.

These need an app context because the browser SDK falls back to the same
``indico.conf`` values as the server-side client.  They do not need a database
and they never touch the network - the CDN URLs are only ever built, not
fetched.
"""

import json
from pathlib import Path

import pytest
from jinja2 import Environment

import indico

import indico_sentry
from indico_sentry.defaults import DEFAULT_SETTINGS
from indico_sentry.frontend import (SDK_INTEGRITY, SDK_VERSION, csp_script_source, frontend_context, frontend_summary,
                                    init_options, is_enabled, script_origin, sdk_script)
from indico_sentry.hooks import SETTINGS


DSN = 'https://frontend@o1.ingest.sentry.io/1'
BACKEND_DSN = 'https://backend@o2.ingest.sentry.io/2'


def settings(**overrides):
    """Plugin settings with the browser SDK switched on."""
    return {**DEFAULT_SETTINGS, 'frontend_enabled': True, 'frontend_dsn': DSN, **overrides}


def render(context):
    """Render the injected snippet without needing an Indico app."""
    source = (Path(indico_sentry.__file__).parent / 'templates' / '_frontend.html').read_text(encoding='utf-8')
    return str(Environment(autoescape=True).from_string(source).module.render_frontend(context))


class TestIsEnabled:
    def test_needs_the_switch_and_a_dsn(self):
        assert is_enabled(settings())
        assert not is_enabled(settings(frontend_enabled=False))
        assert not is_enabled(settings(frontend_dsn=''))
        assert not is_enabled(settings(frontend_dsn='   '))

    def test_backend_dsn_does_not_enable_it(self):
        assert not is_enabled(dict(DEFAULT_SETTINGS, dsn=BACKEND_DSN))


class TestInitOptions:
    def test_uses_its_own_dsn(self, app_ctx):
        assert init_options(settings(dsn=BACKEND_DSN))['dsn'] == DSN

    def test_environment_falls_back_to_the_backend_one(self, app_ctx):
        assert init_options(settings(environment='staging'))['environment'] == 'staging'
        assert init_options(settings(environment='staging',
                                     frontend_environment='browser'))['environment'] == 'browser'

    def test_environment_falls_back_to_indico_conf(self, make_app_ctx):
        make_app_ctx(SENTRY_ENVIRONMENT='qa')
        assert init_options(settings())['environment'] == 'qa'

    def test_release_falls_back_to_the_backend_one(self, app_ctx):
        assert init_options(settings(release='1.2.3'))['release'] == '1.2.3'
        assert init_options(settings(release='1.2.3', frontend_release='4.5.6'))['release'] == '4.5.6'
        assert init_options(settings())['release'] == indico.__version__

    def test_privacy_is_shared_with_the_backend(self, app_ctx):
        assert init_options(settings())['sendDefaultPii'] is True
        assert init_options(settings(send_default_pii=False))['sendDefaultPii'] is False

    def test_sample_rates_are_clamped(self, app_ctx):
        assert init_options(settings(frontend_traces_sample_rate=0.25))['tracesSampleRate'] == 0.25
        assert init_options(settings(frontend_traces_sample_rate=5))['tracesSampleRate'] == 1.0
        assert init_options(settings(frontend_traces_sample_rate=-1))['tracesSampleRate'] == 0.0
        assert init_options(settings(frontend_traces_sample_rate=None))['tracesSampleRate'] == 0.0
        assert init_options(settings(frontend_traces_sample_rate='nonsense'))['tracesSampleRate'] == 0.0

    def test_filter_lists_are_passed_through(self, app_ctx):
        options = init_options(settings())
        assert 'ResizeObserver loop limit exceeded' in options['ignoreErrors']
        assert 'chrome-extension://' in options['denyUrls']

    @pytest.mark.parametrize(('message', 'issue'), (
        ('Error invoking postMessage: Java object is gone', 'Instagram Android in-app browser'),
        ("undefined is not an object (evaluating 'window.webkit.messageHandlers')", 'Instagram iOS in-app browser'),
        ("undefined is not an object (evaluating 'r[\"@context\"].toLowerCase')", "Safari's JSON-LD reader"),
        ('AxiosError: Network Error', 'a dropped connection seen through axios'),
    ))
    def test_default_ignored_errors_cover_known_noise(self, app_ctx, message, issue):
        # The SDK drops an error whose message merely *contains* an entry.
        ignored = init_options(settings())['ignoreErrors']
        assert any(entry in message for entry in ignored), issue

    def test_default_ignored_errors_keep_real_failures(self, app_ctx):
        ignored = init_options(settings())['ignoreErrors']
        for message in ("Cannot read properties of undefined (reading 'foo')",
                        'AxiosError: Request failed with status code 500',
                        '$ is not defined'):
            assert not any(entry in message for entry in ignored), message

    def test_default_denied_urls_cover_in_app_browsers(self, app_ctx):
        denied = init_options(settings())['denyUrls']
        assert any(entry in 'iabjs://navigation_performance_logger_android' for entry in denied)
        assert not any(entry in 'https://event.example.com/static/dist/js/main.js' for entry in denied)

    def test_empty_lists_are_left_out(self, app_ctx):
        # An empty option is not the same as an unset one; `tracePropagationTargets: []`
        # would stop the browser from propagating traces at all.
        options = init_options(settings(frontend_ignored_errors=[], frontend_denied_urls=[],
                                        frontend_trace_propagation_targets=['', '  ']))
        assert 'ignoreErrors' not in options
        assert 'denyUrls' not in options
        assert 'tracePropagationTargets' not in options

    def test_trace_propagation_targets(self, app_ctx):
        options = init_options(settings(frontend_trace_propagation_targets=['https://indico.example.com', ' /api ']))
        assert options['tracePropagationTargets'] == ['https://indico.example.com', '/api']

    def test_options_are_json_serializable(self, app_ctx):
        # They are rendered into the page with `|tojson`.
        json.dumps(init_options(settings()))


class TestSdkScript:
    def test_plain_bundle_by_default(self, app_ctx):
        url, integrity = sdk_script(settings())
        assert url == f'https://browser.sentry-cdn.com/{SDK_VERSION}/bundle.min.js'
        assert integrity == SDK_INTEGRITY['bundle.min.js']

    def test_tracing_bundle_when_tracing_is_on(self, app_ctx):
        url, integrity = sdk_script(settings(frontend_traces_sample_rate=0.1))
        assert url.endswith('/bundle.tracing.min.js')
        assert integrity == SDK_INTEGRITY['bundle.tracing.min.js']

    def test_replay_bundle_when_only_replay_is_on(self, app_ctx):
        url = sdk_script(settings(frontend_replay_error_sample_rate=1.0))[0]
        assert url.endswith('/bundle.replay.min.js')

    def test_combined_bundle(self, app_ctx):
        url = sdk_script(settings(frontend_traces_sample_rate=0.1, frontend_replay_session_sample_rate=0.01))[0]
        assert url.endswith('/bundle.tracing.replay.min.js')

    def test_every_bundle_has_a_checksum(self):
        assert all(value.startswith('sha384-') for value in SDK_INTEGRITY.values())

    def test_custom_url_is_used_verbatim_and_unchecked(self, app_ctx):
        url, integrity = sdk_script(settings(frontend_sdk_url='https://cdn.example.com/sentry.js'))
        assert url == 'https://cdn.example.com/sentry.js'
        assert integrity is None

    def test_script_origin(self, app_ctx):
        assert script_origin(settings()) == 'https://browser.sentry-cdn.com'
        assert script_origin(settings(frontend_sdk_url='https://cdn.example.com/a/b.js')) == 'https://cdn.example.com'

    def test_script_origin_of_a_relative_url(self, app_ctx):
        # A self-hosted copy served by Indico itself is already covered by 'self'.
        assert script_origin(settings(frontend_sdk_url='/static/sentry.js')) is None


class TestCspScriptSource:
    def test_none_while_disabled(self, app_ctx):
        SETTINGS.prime(dict(DEFAULT_SETTINGS))
        assert csp_script_source() is None

    def test_cdn_host_when_enabled(self, app_ctx):
        SETTINGS.prime(settings())
        assert csp_script_source() == 'https://browser.sentry-cdn.com'


class TestFrontendContext:
    def test_nothing_while_disabled(self, app_ctx):
        assert frontend_context(dict(DEFAULT_SETTINGS)) is None
        assert frontend_context(settings(frontend_enabled=False)) is None
        assert frontend_context(settings(frontend_dsn='')) is None

    def test_falls_back_to_the_settings_cache(self, app_ctx):
        SETTINGS.prime(settings())
        assert frontend_context() is not None

    def test_context_shape(self, app_ctx):
        context = frontend_context(settings())
        assert context['sdk_url'].startswith('https://browser.sentry-cdn.com/')
        assert context['sdk_integrity']
        assert context['options']['dsn'] == DSN
        assert context['replay'] == {'maskAllText': True, 'blockAllMedia': True}
        # No request context in the tests, so there is no user and no endpoint.
        assert context['user'] is None
        assert context['tags'] == {}

    def test_no_user_without_pii(self, app_ctx):
        assert frontend_context(settings(send_default_pii=False))['user'] is None

    def test_no_trace_meta_without_a_sampled_transaction(self, app_ctx):
        assert frontend_context(settings())['trace'] is None

    def test_broken_settings_do_not_raise(self, app_ctx):
        # Whatever ends up in the database, a page must still render.
        assert frontend_context({'frontend_enabled': True, 'frontend_dsn': DSN, 'max_breadcrumbs': 'nope'}) is None


class TestSnippet:
    def test_renders_the_sdk_and_the_options(self, app_ctx):
        html = render(frontend_context(settings(frontend_traces_sample_rate=0.5)))
        assert 'bundle.tracing.min.js' in html
        assert 'integrity="sha384-' in html
        assert 'crossorigin="anonymous"' in html
        assert DSN in html
        assert 'Sentry.init(options)' in html

    def test_inline_script_can_get_a_csp_nonce(self):
        # Indico rewrites a bare `<script>` into `<script nonce="...">`; an
        # opening tag with attributes would silently miss out on that.
        source = (Path(indico_sentry.__file__).parent / 'templates' / '_frontend.html').read_text(encoding='utf-8')
        assert '\n    <script>\n' in source

    def test_default_filters_reach_the_page(self, app_ctx):
        html = render(frontend_context(settings()))
        assert 'Java object is gone' in html
        assert 'iabjs://' in html

    def test_buffered_errors_are_reported_as_uncaught(self, app_ctx):
        # Errors from before the SDK loaded are replayed afterwards; they must
        # not be passed off as caught by the application.
        html = render(frontend_context(settings()))
        assert 'handled: false' in html
        assert 'auto.browser.global_handlers.' in html
        assert 'indico_sentry.buffered' in html

    def test_a_page_without_its_bundles_is_reported_once(self, app_ctx):
        html = render(frontend_context(settings()))
        assert "typeof window.jQuery === 'undefined'" in html
        assert 'Indico page bundles did not load' in html

    def test_the_smallest_bundle_is_used_by_default(self, app_ctx):
        html = render(frontend_context(settings()))
        assert 'bundle.min.js' in html
        assert 'bundle.tracing' not in html
        assert 'bundle.replay' not in html

    def test_trace_meta_tags_are_left_out_when_unavailable(self, app_ctx):
        html = render(frontend_context(settings()))
        assert 'name="sentry-trace"' not in html

    def test_trace_meta_tags_are_rendered(self, app_ctx):
        context = frontend_context(settings())
        context['trace'] = {'sentry_trace': 'abc-def-1', 'baggage': 'sentry-environment=test'}
        html = render(context)
        assert '<meta name="sentry-trace" content="abc-def-1">' in html
        assert '<meta name="baggage" content="sentry-environment=test">' in html

    def test_dsn_is_escaped_into_json(self, app_ctx):
        context = frontend_context(settings())
        context['tags'] = {'indico.endpoint': '</script><script>alert(1)</script>'}
        html = render(context)
        assert '<script>alert(1)</script>' not in html


class TestFrontendSummary:
    def test_disabled(self, app_ctx):
        summary = frontend_summary(dict(DEFAULT_SETTINGS))
        assert not summary['enabled']
        assert not summary['requested']
        assert summary['dsn_host'] is None

    def test_enabled_without_a_dsn(self, app_ctx):
        summary = frontend_summary(settings(frontend_dsn=''))
        assert summary['requested']
        assert not summary['enabled']

    def test_reports_what_admins_need_for_a_csp(self, app_ctx):
        summary = frontend_summary(settings(frontend_replay_session_sample_rate=0.1))
        assert summary['enabled']
        assert summary['dsn_host'] == 'o1.ingest.sentry.io'
        assert summary['csp_script_src'] == 'https://browser.sentry-cdn.com'
        assert summary['csp_connect_src'] == 'https://o1.ingest.sentry.io'
        assert summary['sdk_version'] == SDK_VERSION
        assert summary['replay']

    def test_custom_url_has_no_version(self, app_ctx):
        assert frontend_summary(settings(frontend_sdk_url='https://cdn.example.com/s.js'))['sdk_version'] is None

"""Tests for the Sentry hooks.

The hooks are the part of the plugin that runs on every event, so they are
tested against plain dicts shaped like real Sentry payloads.
"""

import pytest

from indico_sentry import hooks
from indico_sentry.defaults import DEFAULT_SETTINGS


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """Give every test a fresh, fully-populated settings dict."""
    data = dict(DEFAULT_SETTINGS)
    monkeypatch.setattr(hooks.SETTINGS, 'get', lambda: data)
    return data


def broken_settings():
    raise RuntimeError('settings are unavailable')


def make_event(**kwargs):
    event = {'transaction': 'events.display', 'extra': {}, 'request': {}}
    event.update(kwargs)
    return event


class TestBeforeSendFiltering:
    def test_passes_normal_event_through(self):
        event = make_event()
        assert hooks.before_send(event, {}) is event

    def test_drops_ignored_transaction(self, settings):
        settings['ignored_transactions'] = ['assets.*']
        assert hooks.before_send(make_event(transaction='assets.image'), {}) is None

    def test_uses_endpoint_extra_when_transaction_missing(self, settings):
        settings['ignored_transactions'] = ['core.ping']
        event = make_event(transaction=None, extra={'Endpoint': 'core.ping'})
        assert hooks.before_send(event, {}) is None

    def test_drops_ignored_logger(self, settings):
        settings['ignored_loggers'] = ['werkzeug']
        assert hooks.before_send(make_event(logger='werkzeug'), {}) is None

    def test_drops_ignored_logger_from_hint(self, settings):
        class Record:
            name = 'noisy.logger'

        settings['ignored_loggers'] = ['noisy.*']
        assert hooks.before_send(make_event(), {'log_record': Record()}) is None

    def test_drops_ignored_exception(self, settings):
        settings['ignored_exceptions'] = ['ValueError']
        hint = {'exc_info': (ValueError, ValueError('boom'), None)}
        assert hooks.before_send(make_event(), hint) is None

    def test_drops_subclass_of_ignored_exception(self, settings):
        class MyError(ValueError):
            pass

        settings['ignored_exceptions'] = ['builtins.ValueError']
        hint = {'exc_info': (MyError, MyError('boom'), None)}
        assert hooks.before_send(make_event(), hint) is None

    def test_keeps_unrelated_exception(self, settings):
        settings['ignored_exceptions'] = ['ValueError']
        hint = {'exc_info': (KeyError, KeyError('boom'), None)}
        assert hooks.before_send(make_event(), hint) is not None

    def test_never_drops_on_internal_error(self, monkeypatch):
        # If the settings blow up we would rather send an unfiltered event
        # than lose it.
        monkeypatch.setattr(hooks.SETTINGS, 'get', broken_settings)
        event = make_event()
        assert hooks.before_send(event, {}) is event


class TestBeforeSendPrivacy:
    def test_strips_everything_when_pii_disabled(self, settings):
        settings['send_default_pii'] = False
        event = make_event(
            user={'id': 1, 'email': 'a@b.c'},
            request={'cookies': {'session': 'x'}, 'data': {'a': 1},
                     'headers': {'Authorization': 'Bearer x', 'Accept': '*/*'}},
        )
        result = hooks.before_send(event, {})
        assert 'user' not in result
        assert 'cookies' not in result['request']
        assert 'data' not in result['request']
        assert 'Authorization' not in result['request']['headers']
        assert result['request']['headers']['Accept'] == '*/*'

    def test_keeps_user_id_when_email_disabled(self, settings):
        settings['send_user_email'] = False
        event = make_event(user={'id': 42, 'email': 'a@b.c', 'name': 'A', 'ip_address': '1.2.3.4'})
        result = hooks.before_send(event, {})
        assert result['user'] == {'id': 42}

    def test_drops_request_body_when_disabled(self, settings):
        settings['send_request_body'] = False
        event = make_event(request={'data': {'a': 1}, 'headers': {}})
        assert 'data' not in hooks.before_send(event, {})['request']

    def test_keeps_data_by_default(self):
        event = make_event(request={'data': {'title': 'My event'}})
        assert hooks.before_send(event, {})['request']['data'] == {'title': 'My event'}


class TestBeforeSendScrubbing:
    def test_scrubs_request_data(self):
        event = make_event(request={'data': {'password': 'hunter2', 'title': 'ok'}})
        result = hooks.before_send(event, {})
        assert result['request']['data']['password'] == '[Filtered]'
        assert result['request']['data']['title'] == 'ok'

    def test_scrubs_query_string(self):
        event = make_event(request={'query_string': 'q=hello&access_token=secret'})
        result = hooks.before_send(event, {})
        assert 'secret' not in result['request']['query_string']
        assert 'q=hello' in result['request']['query_string']

    def test_scrubs_breadcrumb_data(self):
        event = make_event(breadcrumbs={'values': [{'data': {'api_key': 'abc'}}]})
        result = hooks.before_send(event, {})
        assert result['breadcrumbs']['values'][0]['data']['api_key'] == '[Filtered]'

    def test_scrubs_extra(self):
        event = make_event(extra={'csrf_token': 'abc', 'Endpoint': 'events.display'})
        result = hooks.before_send(event, {})
        assert result['extra']['csrf_token'] == '[Filtered]'
        assert result['extra']['Endpoint'] == 'events.display'

    def test_no_scrub_fields_leaves_event_alone(self, settings):
        settings['scrub_fields'] = []
        event = make_event(request={'data': {'password': 'hunter2'}})
        assert hooks.before_send(event, {})['request']['data']['password'] == 'hunter2'

    def test_malformed_query_string_is_kept(self):
        event = make_event(request={'query_string': '%%%'})
        assert hooks.before_send(event, {})['request']['query_string'] is not None


class TestBeforeSendTransaction:
    def test_drops_ignored_transaction(self, settings):
        settings['ignored_transactions'] = ['assets.*']
        assert hooks.before_send_transaction(make_event(transaction='assets.js'), {}) is None

    def test_scrubs_transactions_too(self):
        event = make_event(transaction='events.display', request={'data': {'password': 'x'}})
        result = hooks.before_send_transaction(event, {})
        assert result['request']['data']['password'] == '[Filtered]'


class TestSamplers:
    def ctx(self, name, **kwargs):
        return {'transaction_context': {'name': name}, **kwargs}

    def test_default_rate(self, settings):
        settings['traces_sample_rate'] = 0.25
        assert hooks.traces_sampler(self.ctx('events.display')) == 0.25

    def test_disabled_by_default(self):
        assert hooks.traces_sampler(self.ctx('events.display')) == 0.0

    def test_ignored_transaction_is_never_sampled(self, settings):
        settings['traces_sample_rate'] = 1.0
        settings['ignored_transactions'] = ['assets.*']
        assert hooks.traces_sampler(self.ctx('assets.js')) == 0.0

    def test_per_endpoint_override(self, settings):
        settings['traces_sample_rate'] = 0.1
        settings['sample_rate_overrides'] = ['events.display = 1.0']
        assert hooks.traces_sampler(self.ctx('events.display')) == 1.0
        assert hooks.traces_sampler(self.ctx('events.other')) == 0.1

    def test_prefix_override(self, settings):
        settings['sample_rate_overrides'] = ['api.* = 0.5']
        assert hooks.traces_sampler(self.ctx('api.events')) == 0.5

    def test_falls_back_to_wsgi_path(self, settings):
        settings['ignored_transactions'] = ['/ping']
        ctx = {'transaction_context': {}, 'wsgi_environ': {'PATH_INFO': '/ping'}}
        assert hooks.traces_sampler(ctx) == 0.0

    def test_inherits_parent_sampling_decision(self, settings):
        settings['traces_sample_rate'] = 0.0
        assert hooks.traces_sampler(self.ctx('events.display', parent_sampled=True)) == 1.0
        settings['traces_sample_rate'] = 1.0
        assert hooks.traces_sampler(self.ctx('events.display', parent_sampled=False)) == 0.0

    def test_profiles_sampler_uses_its_own_rate(self, settings):
        settings['traces_sample_rate'] = 1.0
        settings['profiles_sample_rate'] = 0.3
        assert hooks.profiles_sampler(self.ctx('events.display')) == 0.3

    def test_sampler_never_raises(self, monkeypatch):
        monkeypatch.setattr(hooks.SETTINGS, 'get', broken_settings)
        assert hooks.traces_sampler({}) == 0.0

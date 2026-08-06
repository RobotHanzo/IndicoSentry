"""Tests for the pure helpers.

These do not need an Indico app or a database.
"""

import threading

import pytest

from indico_sentry.util import (SettingsCache, exception_names, matches_any, parse_sample_overrides, reentrancy_guard,
                                scrub_mapping)


class TestMatchesAny:
    @pytest.mark.parametrize(('value', 'patterns', 'expected'), (
        ('events.display', ['events.display'], True),
        ('events.display', ['events.other'], False),
        ('assets.image', ['assets.*'], True),
        ('assets', ['assets.*'], False),
        ('anything', ['*'], True),
        ('events.display', [], False),
        ('events.display', None, False),
        ('', ['*'], False),
        (None, ['*'], False),
    ))
    def test_matching(self, value, patterns, expected):
        assert matches_any(value, patterns) is expected


class TestParseSampleOverrides:
    def test_valid_rules(self):
        assert parse_sample_overrides(['events.display = 0.5', 'assets.*=0']) == {
            'events.display': 0.5,
            'assets.*': 0.0,
        }

    @pytest.mark.parametrize('line', (
        'no-equals-sign',
        'endpoint = notanumber',
        'endpoint = 1.5',      # out of range
        'endpoint = -0.1',     # out of range
        ' = 0.5',              # no name
    ))
    def test_invalid_rules_are_skipped(self, line):
        assert parse_sample_overrides([line]) == {}

    def test_none(self):
        assert parse_sample_overrides(None) == {}


class TestExceptionNames:
    def test_includes_bases(self):
        class BaseError(Exception):
            pass

        class ChildError(BaseError):
            pass

        names = set(exception_names(ChildError()))
        assert 'ChildError' in names
        assert 'BaseError' in names
        assert 'Exception' in names
        assert 'object' not in names

    def test_dotted_names(self):
        assert 'builtins.ValueError' in set(exception_names(ValueError()))


class TestScrubMapping:
    def test_scrubs_nested_keys(self):
        data = {
            'password': 'hunter2',
            'nested': {'api_key': 'abc', 'safe': 'ok'},
            'items': [{'access_token': 'xyz'}],
        }
        scrub_mapping(data, ['password', 'api_key', 'token'])
        assert data['password'] == '[Filtered]'
        assert data['nested']['api_key'] == '[Filtered]'
        assert data['nested']['safe'] == 'ok'
        assert data['items'][0]['access_token'] == '[Filtered]'

    def test_substring_match(self):
        data = {'user_password_confirm': 'x'}
        scrub_mapping(data, ['password'])
        assert data['user_password_confirm'] == '[Filtered]'

    def test_depth_limit_does_not_recurse_forever(self):
        data = {}
        node = data
        for __ in range(50):
            node['child'] = {}
            node = node['child']
        node['password'] = 'deep'
        # Should return without blowing the stack; the deep value stays.
        scrub_mapping(data, ['password'])
        assert node['password'] == 'deep'

    def test_non_mapping_input(self):
        assert scrub_mapping('a string', ['password']) == 'a string'


class TestReentrancyGuard:
    def test_blocks_reentry(self):
        with reentrancy_guard('x') as outer:
            assert outer is True
            with reentrancy_guard('x') as inner:
                assert inner is False

    def test_released_after_exit(self):
        with reentrancy_guard('x'):
            pass
        with reentrancy_guard('x') as ok:
            assert ok is True

    def test_released_on_exception(self):
        with pytest.raises(RuntimeError):
            with reentrancy_guard('x'):
                raise RuntimeError
        with reentrancy_guard('x') as ok:
            assert ok is True

    def test_is_thread_local(self):
        results = []
        with reentrancy_guard('x'):
            def _other():
                with reentrancy_guard('x') as ok:
                    results.append(ok)

            thread = threading.Thread(target=_other)
            thread.start()
            thread.join()
        assert results == [True]


class TestSettingsCache:
    def test_falls_back_to_defaults_without_app(self):
        cache = SettingsCache({'foo': 'bar'})
        # No app context here, so loading the real settings fails.
        assert cache.get() == {'foo': 'bar'}

    def test_uses_loaded_value(self, monkeypatch):
        cache = SettingsCache({'foo': 'bar'})
        monkeypatch.setattr(cache, '_load', lambda: {'foo': 'baz'})
        assert cache.get()['foo'] == 'baz'

    def test_caches_within_ttl(self):
        cache = SettingsCache({'n': 0})
        calls = []

        def _load():
            calls.append(1)
            return {'n': len(calls)}

        cache._load = _load
        assert cache.get()['n'] == 1
        assert cache.get()['n'] == 1
        assert len(calls) == 1

    def test_invalidate_forces_reload(self):
        cache = SettingsCache({'n': 0})
        calls = []

        def _load():
            calls.append(1)
            return {'n': len(calls)}

        cache._load = _load
        assert cache.get()['n'] == 1
        cache.invalidate()
        assert cache.get()['n'] == 2

    def test_keeps_last_known_value_when_loading_fails(self):
        cache = SettingsCache({'n': 0})
        cache._load = lambda: {'n': 1}
        assert cache.get()['n'] == 1
        cache.invalidate()
        cache._load = lambda: None  # simulates a DB failure
        assert cache.get()['n'] == 1

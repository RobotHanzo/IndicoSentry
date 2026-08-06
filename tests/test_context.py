"""Tests for the Indico context attached to events."""

import sentry_sdk

from indico_sentry.client import init_sentry
from indico_sentry.context import set_rh_context
from indico_sentry.defaults import DEFAULT_SETTINGS


DSN = 'https://abc123@o1.ingest.sentry.io/2'


def settings(**overrides):
    return dict(DEFAULT_SETTINGS, **overrides)


class FakeCategory:
    id = 7
    title = 'Workshops'
    url = '/category/7/'


class FakeEvent:
    id = 42
    title = 'Annual Meeting'
    type = 'conference'
    external_url = 'https://indico.example.org/event/42/'
    category = FakeCategory()


class FakeRH:
    event = FakeEvent()


class FakeCategoryRH:
    category = FakeCategory()


class ExplodingRH:
    @property
    def event(self):
        raise RuntimeError('the database is on fire')


def capture_with_context(rh):
    """Attach the context for `rh`, then capture an event and return it."""
    with sentry_sdk.isolation_scope():
        set_rh_context(type(rh), rh=rh)
        sentry_sdk.capture_event({'message': 'test'})


class TestEventContext:
    def test_tags_the_event(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        capture_with_context(FakeRH())
        event = transport.events[0]
        assert event['tags']['indico.event_id'] == 42
        assert event['contexts']['Indico Event']['Title'] == 'Annual Meeting'
        assert event['contexts']['Indico Event']['URL'] == 'https://indico.example.org/event/42/'
        assert event['contexts']['Indico Event']['Category'] == '#7 Workshops'

    def test_tags_the_category(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        capture_with_context(FakeCategoryRH())
        event = transport.events[0]
        assert event['tags']['indico.category_id'] == 7
        assert event['contexts']['Indico Category']['Title'] == 'Workshops'

    def test_can_be_disabled(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN, event_context=False), transport=transport)
        capture_with_context(FakeRH())
        assert 'indico.event_id' not in transport.events[0].get('tags', {})

    def test_category_context_can_be_disabled(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN, category_context=False), transport=transport)
        capture_with_context(FakeCategoryRH())
        assert 'indico.category_id' not in transport.events[0].get('tags', {})

    def test_rh_without_an_event(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        capture_with_context(object())
        assert 'indico.event_id' not in transport.events[0].get('tags', {})

    def test_broken_property_does_not_raise(self, app_ctx, transport):
        init_sentry(settings(dsn=DSN), transport=transport)
        # An RH whose `event` blows up must not turn into a second error while
        # the first one is being reported.
        capture_with_context(ExplodingRH())
        assert len(transport.events) == 1

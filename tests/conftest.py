import os

import pytest
import sentry_sdk
from flask import Flask
from sentry_sdk.transport import Transport


# Indico's own pytest plugin points this at /dev/null, but it is disabled for
# this suite (see `addopts` in pyproject.toml), so set it ourselves.
os.environ.setdefault('INDICO_CONFIG', os.devnull)


class RecordingTransport(Transport):
    """Collects events instead of sending them."""

    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        if (event := envelope.get_event()) is not None:
            self.events.append(event)


@pytest.fixture(autouse=True)
def clean_settings_cache():
    """Keep primed settings from leaking between tests."""
    from indico_sentry.hooks import SETTINGS

    SETTINGS.invalidate()
    yield
    SETTINGS.invalidate()


@pytest.fixture
def make_app_ctx():
    """Build a minimal app context with Indico's default configuration.

    Enough for the code that reads ``indico.core.config``; no database.  The
    config is immutable once loaded, so overrides have to be passed in here.
    """
    from indico.core.config import load_config

    contexts = []

    def _make(**overrides):
        app = Flask('indico_sentry_tests')
        app.config['INDICO'] = load_config(only_defaults=True,
                                           override={'BASE_URL': 'http://localhost', **overrides})
        ctx = app.app_context()
        ctx.push()
        contexts.append(ctx)
        return app

    yield _make
    for ctx in reversed(contexts):
        ctx.pop()


@pytest.fixture
def app_ctx(make_app_ctx):
    return make_app_ctx()


@pytest.fixture
def transport():
    """A transport that records events, plus cleanup of the client it gets bound to."""
    recorder = RecordingTransport()
    scope = sentry_sdk.get_global_scope()
    original = scope.client
    yield recorder
    new_client = sentry_sdk.get_client()
    if new_client is not original:
        new_client.close(timeout=0)
    scope.set_client(original)

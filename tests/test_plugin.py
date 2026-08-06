"""Tests for the plugin wiring.

These mostly guard against the settings form, the defaults and the code
drifting apart, which would only show up as a broken admin page at runtime.
"""

from pathlib import Path

import pytest
from jinja2 import Environment
from wtforms.fields.core import UnboundField

import indico_sentry
from indico_sentry.blueprint import blueprint
from indico_sentry.defaults import DEFAULT_SETTINGS, LOGGING_LEVELS
from indico_sentry.forms import SettingsForm
from indico_sentry.plugin import SentryPlugin
from indico_sentry.util import INIT_ONLY_SETTINGS


def form_field_names():
    names = set()
    for klass in reversed(SettingsForm.__mro__):
        names |= {name for name, attr in vars(klass).items() if isinstance(attr, UnboundField)}
    return names


class TestPluginMetadata:
    def test_is_configurable(self):
        assert SentryPlugin.configurable
        assert SentryPlugin.settings_form is SettingsForm

    def test_entry_point_resolves(self):
        from importlib.metadata import entry_points
        ep = next(ep for ep in entry_points(group='indico.plugins') if ep.name == 'sentry')
        assert ep.load() is SentryPlugin

    def test_has_a_version(self):
        assert indico_sentry.__version__

    def test_has_no_category(self):
        # Indico's RHPlugins fills the PluginCategory.other bucket with the
        # *uncategorised* plugins, overwriting whatever declared that category
        # explicitly - such a plugin silently vanishes from the admin list.
        assert SentryPlugin.category is None


class TestSettingsMatchForm:
    def test_every_form_field_has_a_default(self):
        missing = form_field_names() - set(DEFAULT_SETTINGS)
        assert not missing, f'form fields without a default: {sorted(missing)}'

    def test_every_default_has_a_form_field(self):
        # Anything without a field can never be changed by an admin.
        missing = set(DEFAULT_SETTINGS) - form_field_names()
        assert not missing, f'settings with no form field: {sorted(missing)}'

    def test_fieldsets_cover_every_field(self):
        in_fieldsets = {name for _, fields in SettingsForm._fieldsets for name in fields}
        assert in_fieldsets == form_field_names()

    def test_fieldsets_have_no_duplicates(self):
        names = [name for _, fields in SettingsForm._fieldsets for name in fields]
        assert len(names) == len(set(names))

    def test_field_opts_reference_real_fields(self):
        assert not set(SentryPlugin.settings_form_field_opts) - form_field_names()

    def test_init_only_settings_exist(self):
        missing = INIT_ONLY_SETTINGS - set(DEFAULT_SETTINGS)
        assert not missing, f'unknown init-only settings: {sorted(missing)}'

    def test_defaults_are_json_serializable(self):
        # Indico stores plugin settings as JSON.
        import json
        json.dumps(DEFAULT_SETTINGS)

    def test_default_logging_level_is_offered(self):
        assert DEFAULT_SETTINGS['logging_level'] in LOGGING_LEVELS

    def test_plugin_defaults_are_a_copy(self):
        # A shared mutable default would leak edits between the plugin and the
        # module-level defaults used by the hooks.
        assert SentryPlugin.default_settings is not DEFAULT_SETTINGS
        assert SentryPlugin.default_settings['scrub_fields'] is not DEFAULT_SETTINGS['scrub_fields']


class TestBlueprint:
    def test_name_matches_plugin(self):
        # IndicoPluginBlueprint prefixes the name, which is why the views are
        # reached via url_for_plugin('sentry.<endpoint>').
        assert blueprint.name == 'plugin_sentry'

    def test_routes_are_registered(self):
        app = _FakeApp()
        for deferred in blueprint.deferred_functions:
            deferred(_FakeSetupState(app))
        assert set(app.rules) == {'/admin/plugins/sentry/test', '/admin/plugins/sentry/reload'}


class TestTemplate:
    def test_details_template_parses(self):
        source = (Path(indico_sentry.__file__).parent / 'templates' / '_details.html').read_text(encoding='utf-8')
        env = Environment(extensions=['jinja2.ext.i18n'], autoescape=True)
        env.parse(source)

    def test_details_template_defines_the_macro(self):
        source = (Path(indico_sentry.__file__).parent / 'templates' / '_details.html').read_text(encoding='utf-8')
        assert 'macro render_status(' in source


class _FakeSetupState:
    """Just enough of a Flask setup state to capture the registered rules."""

    def __init__(self, app):
        self.app = app
        self.url_prefix = blueprint.url_prefix
        self.name = blueprint.name
        self.subdomain = None
        self.url_defaults = {}
        self.first_registration = True
        self.blueprint = blueprint
        self.options = {}

    def add_url_rule(self, rule, endpoint=None, view_func=None, **options):
        self.app.rules.append(f'{self.url_prefix}{rule}')


class _FakeApp:
    def __init__(self):
        self.rules = []


@pytest.fixture(autouse=True)
def _no_app_needed():
    """These tests deliberately avoid needing an Indico app."""

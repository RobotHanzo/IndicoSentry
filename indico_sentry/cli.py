"""``indico sentry`` CLI commands."""

import click
import sentry_sdk

from indico.cli.core import cli_group

from indico_sentry.client import is_sending, status
from indico_sentry.hooks import SETTINGS
from indico_sentry.util import INIT_ONLY_SETTINGS


@cli_group(name='sentry')
def cli():
    """Manage the Sentry integration."""


@cli.command()
def info():
    """Show the Sentry configuration active in this process."""
    click.echo(f'sentry-sdk:    {getattr(sentry_sdk, "VERSION", "?")}')
    click.echo(f'sending:       {_yesno(is_sending())}')
    click.echo(f'initialized:   {_yesno(status["initialized"])}')
    click.echo(f'DSN:           {status["dsn_host"] or "(not configured)"} (from {status["dsn_source"] or "n/a"})')
    click.echo(f'environment:   {status["environment"]}')
    click.echo(f'release:       {status["release"]}')
    click.echo(f'integrations:  {", ".join(status["integrations"]) or "(none)"}')
    if status['error']:
        click.secho(f'error:         {status["error"]}', fg='red')

    settings = SETTINGS.get()
    click.echo('\nSettings (* = requires a restart to take effect):')
    for key in sorted(settings):
        marker = '*' if key in INIT_ONLY_SETTINGS else ' '
        value = '<set>' if key == 'dsn' and settings[key] else settings[key]
        click.echo(f'  {marker} {key} = {value}')


@cli.command()
@click.option('--crash', is_flag=True, help='Send a real exception instead of a message.')
def test(crash):
    """Send a test event to Sentry."""
    if not is_sending():
        raise click.ClickException('Sentry is not active. Configure a DSN in the plugin settings or indico.conf.')
    client = sentry_sdk.get_client()

    with sentry_sdk.new_scope() as scope:
        scope.set_tag('indico.test_event', 'true')
        if crash:
            try:
                raise RuntimeError('Indico Sentry plugin test exception')
            except RuntimeError:
                event_id = sentry_sdk.capture_exception()
        else:
            scope.set_level('info')
            event_id = sentry_sdk.capture_message('Indico Sentry plugin test event')

    client.flush(timeout=10.0)
    if not event_id:
        raise click.ClickException('The event was not sent; it was most likely dropped by a filter.')
    click.secho(f'Sent event {event_id}', fg='green')


def _yesno(value):
    return 'yes' if value else 'no'

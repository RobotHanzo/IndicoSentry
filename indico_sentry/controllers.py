"""Admin-only actions available from the plugin settings page."""

import sentry_sdk
from flask import flash, redirect, session

from indico.modules.admin import RHAdminBase
from indico.web.flask.util import url_for

from indico_sentry import _
from indico_sentry.client import is_sending, reinit_sentry, status


class RHSentryBase(RHAdminBase):
    @property
    def back_url(self):
        return url_for('plugins.details', plugin='sentry')


class RHSentryTest(RHSentryBase):
    """Send a test event so admins can verify the DSN actually works."""

    def _process(self):
        if not is_sending():
            flash(_('Sentry is not active in this process. Check that a DSN is configured and that Indico was '
                    'restarted after setting it.'), 'error')
            return redirect(self.back_url)
        client = sentry_sdk.get_client()

        with sentry_sdk.new_scope() as scope:
            scope.set_tag('indico.test_event', 'true')
            scope.set_level('info')
            event_id = sentry_sdk.capture_message(
                f'Indico Sentry plugin test event (triggered by {session.user})'
            )
        client.flush(timeout=5.0)

        if event_id:
            flash(_('Test event sent to Sentry (event id {}). It should appear in your project within a few '
                    'seconds.').format(event_id), 'success')
        else:
            flash(_('The test event was not sent. It was most likely dropped by a filter.'), 'warning')
        return redirect(self.back_url)


class RHSentryReload(RHSentryBase):
    """Rebuild the Sentry client in this worker from the saved settings."""

    def _process(self):
        if reinit_sentry():
            flash(_('Sentry was reloaded in this worker process. Restart Indico to apply the change to all '
                    'workers.'), 'success')
        else:
            flash(_('Sentry could not be reloaded: {}').format(status.get('error') or 'unknown error'), 'error')
        return redirect(self.back_url)

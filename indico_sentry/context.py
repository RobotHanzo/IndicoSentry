"""Indico-specific context attached to Sentry events.

Core already tags the endpoint, request id, locale, user and RH class.  This
module adds the objects an Indico admin actually needs to reproduce a problem:
which event, category or contribution was being accessed, and which Celery
task was running.
"""

import logging

import sentry_sdk

from indico_sentry.hooks import SETTINGS


logger = logging.getLogger('indico.plugin.sentry')


def set_rh_context(sender, rh, **kwargs):
    """Attach event/category context after an RH resolved its arguments."""
    try:
        settings = SETTINGS.get()
        if settings.get('event_context', True):
            _set_event_context(rh)
        if settings.get('category_context', True):
            _set_category_context(rh)
    except Exception:
        # Context is a nice-to-have; never let it break request handling.
        logger.debug('Could not set Sentry context', exc_info=True)


def _set_event_context(rh):
    event = _safe_getattr(rh, 'event')
    if event is None:
        return
    sentry_sdk.set_tag('indico.event_id', event.id)
    data = {
        'ID': event.id,
        'Title': event.title,
        'Type': _safe_getattr(event, 'type'),
        'URL': _safe_getattr(event, 'external_url'),
    }
    if (category := _safe_getattr(event, 'category')) is not None:
        data['Category'] = f'#{category.id} {category.title}'
    sentry_sdk.set_context('Indico Event', {k: v for k, v in data.items() if v is not None})

    if (contribution := _safe_getattr(rh, 'contrib')) is not None:
        sentry_sdk.set_tag('indico.contribution_id', contribution.id)
    if (registration := _safe_getattr(rh, 'registration')) is not None:
        sentry_sdk.set_tag('indico.registration_id', registration.id)


def _set_category_context(rh):
    category = _safe_getattr(rh, 'category')
    if category is None:
        return
    sentry_sdk.set_tag('indico.category_id', category.id)
    sentry_sdk.set_context('Indico Category', {
        'ID': category.id,
        'Title': category.title,
        'URL': _safe_getattr(category, 'url'),
    })


def _safe_getattr(obj, name):
    """``getattr`` that tolerates properties raising or hitting the DB."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None

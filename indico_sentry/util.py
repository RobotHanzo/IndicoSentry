"""Helpers shared by the Sentry plugin.

The functions in here are called from inside Sentry hooks (``before_send``,
``traces_sampler``, ...) which may run in odd situations: outside an app
context, while the database is unreachable, or while another Sentry event is
already being processed.  Everything here therefore has to be defensive and
must never raise.
"""

import threading
import time
from contextlib import contextmanager


#: How long runtime settings are cached inside a process before being re-read
#: from the database.  Sentry hooks run on the hot path, so we do not want to
#: hit the DB for every single event.
SETTINGS_CACHE_TTL = 30

#: Settings that are only read when the Sentry client is created and therefore
#: require a restart (or worker reload) to take effect.
INIT_ONLY_SETTINGS = frozenset({
    'dsn', 'environment', 'release', 'server_name', 'logging_level', 'max_breadcrumbs',
    'attach_stacktrace', 'enable_logging', 'enable_celery', 'enable_sqlalchemy', 'enable_redis',
})

_local = threading.local()


@contextmanager
def reentrancy_guard(name):
    """Prevent a Sentry hook from re-entering itself.

    Yields ``False`` when the guarded section is already running in this
    thread.  Reading the plugin settings touches the database, and a database
    error gets logged, which - with the logging integration enabled - produces
    another Sentry event whose ``before_send`` would read the settings again.
    """
    active = getattr(_local, 'active', None)
    if active is None:
        active = _local.active = set()
    if name in active:
        yield False
        return
    active.add(name)
    try:
        yield True
    finally:
        active.discard(name)


class SettingsCache:
    """Process-local cache of the plugin settings with a short TTL.

    Falls back to the last known value (and finally to the defaults) whenever
    the settings cannot be read, e.g. because there is no app context or the
    database is down.
    """

    def __init__(self, defaults):
        self._defaults = defaults
        self._data = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def invalidate(self):
        with self._lock:
            self._expires = 0.0

    def prime(self, data):
        """Seed the cache with settings that were just applied.

        Keeps the hooks and the client in agreement: whatever the client was
        built from is also what ``before_send`` and the samplers use, instead
        of a value the cache happens to be holding.
        """
        with self._lock:
            self._data = dict(data)
            self._expires = time.monotonic() + SETTINGS_CACHE_TTL

    def get(self):
        now = time.monotonic()
        data = self._data
        if data is not None and now < self._expires:
            return data
        fresh = self._load()
        if fresh is None:
            # Never leave the caller without a mapping to work with.
            return data if data is not None else dict(self._defaults)
        with self._lock:
            self._data = fresh
            self._expires = now + SETTINGS_CACHE_TTL
        return fresh

    def _load(self):
        with reentrancy_guard('settings') as ok:
            if not ok:
                return None
            try:
                from indico_sentry.plugin import SentryPlugin
                settings = dict(self._defaults)
                settings.update(SentryPlugin.settings.get_all())
                return settings
            except Exception:
                return None


def parse_sample_overrides(lines):
    """Parse ``endpoint = rate`` lines into a dict.

    Invalid lines are skipped rather than raising, so a bad setting can never
    take down request handling.
    """
    overrides = {}
    for line in lines or ():
        name, sep, rate = line.partition('=')
        if not sep:
            continue
        name = name.strip()
        try:
            value = float(rate.strip())
        except ValueError:
            continue
        if name and 0.0 <= value <= 1.0:
            overrides[name] = value
    return overrides


def matches_any(value, patterns):
    """Check ``value`` against a list of names with optional ``*`` suffix."""
    if not value:
        return False
    for pattern in patterns or ():
        if pattern.endswith('*'):
            if value.startswith(pattern[:-1]):
                return True
        elif value == pattern:
            return True
    return False


def exception_names(exc):
    """Yield the dotted and bare names of an exception class and its bases."""
    seen = set()
    for cls in type(exc).__mro__:
        if cls is object:
            break
        for name in (f'{cls.__module__}.{cls.__qualname__}', cls.__name__):
            if name not in seen:
                seen.add(name)
                yield name


def scrub_mapping(data, keys, replacement='[Filtered]', _depth=0):
    """Recursively replace values of sensitive keys inside nested data."""
    if _depth > 10:
        return data
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and any(k in key.lower() for k in keys):
                data[key] = replacement
            else:
                scrub_mapping(value, keys, replacement, _depth + 1)
    elif isinstance(data, list):
        for item in data:
            scrub_mapping(item, keys, replacement, _depth + 1)
    return data

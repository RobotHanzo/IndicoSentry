# Indico Sentry Plugin

Sends errors and performance data from [Indico](https://getindico.io) to [Sentry](https://sentry.io).

Indico already has basic Sentry support built in: if you set `SENTRY_DSN` in `indico.conf`, errors are
reported through the Flask, logging and Redis integrations. This plugin replaces that client with a
richer one and moves the configuration into the admin area.

## What it adds

| | Indico core | This plugin |
|---|---|---|
| Configuration | `indico.conf` only | Admin UI, with `indico.conf` as fallback |
| Integrations | Flask, logging, Redis, pure_eval | + Celery, + SQLAlchemy (optional) |
| Performance tracing | – | Per-endpoint sample rates |
| Profiling | – | Yes |
| Error filtering | – | By exception, logger or endpoint |
| Data scrubbing | SDK defaults | Configurable field list, query strings, breadcrumbs |
| Indico context | user, endpoint, RH class | + event, category, contribution, registration |
| Test event | – | Button on the settings page and `indico sentry test` |

## Requirements

- Indico 3.3 or newer
- Python 3.12

## Installation

```bash
pip install indico-plugin-sentry
```

Enable it in `indico.conf`:

```python
PLUGINS = {'sentry'}
```

Then restart Indico (and the Celery workers) and open **Administration → Plugins → Sentry**.

## Configuration

All settings live on the plugin page. The DSN can also come from `SENTRY_DSN` in `indico.conf`; the
plugin setting wins when both are set.

### Connection

| Setting | Notes |
|---|---|
| `Sentry DSN` | Falls back to `SENTRY_DSN` from `indico.conf` |
| `Environment` | Falls back to `SENTRY_ENVIRONMENT`, default `production` |
| `Release` | Defaults to the Indico version |
| `Server name` | Overrides the reported hostname |

### Performance

Tracing is off by default. Set **Trace sample rate** to something small (`0.01`–`0.05`) on a busy
instance, then tune individual endpoints:

```
assets.* = 0
events.display = 0.5
api.* = 0.1
```

A trailing `*` matches a prefix. **Profile sample rate** is a fraction *of traced requests*, so it
does nothing while tracing is at `0`.

### Filtering

Three lists, one entry per line:

- **Ignored exceptions** — bare (`NotFound`) or dotted (`werkzeug.exceptions.NotFound`) class names.
  Subclasses are matched too. Defaults cover the HTTP errors that are part of normal operation.
- **Ignored loggers** — logger names, `*` suffix allowed.
- **Ignored endpoints** — Indico endpoint names such as `assets.*` or `core.ping`. Applies to both
  errors and traces.

Transactions are named after the Indico **endpoint** (`events.display`), not the URL rule — that is
what these lists and the sample-rate overrides match against.

### Privacy

- **Send personal data** off → no user, cookies, request body or auth headers leave the server.
- **Send user email and name** off → only the numeric user id is sent, so issues can still be counted
  per user.
- **Scrubbed fields** — any field whose name *contains* one of these fragments is replaced with
  `[Filtered]`, in the request body, cookies, headers, query string, breadcrumbs, tags and extras.

## Which settings need a restart?

Sentry is configured once per worker process, so anything that goes into the client itself needs a
restart of Indico and the Celery workers:

`dsn`, `environment`, `release`, `server_name`, `logging_level`, `max_breadcrumbs`,
`attach_stacktrace`, and the integration toggles.

Everything else — sample rates, filtering, scrubbing, privacy and context options — is evaluated per
event and takes effect within 30 seconds (the settings cache TTL). The settings page tells you which
saved settings the current worker has not picked up yet.

**Reload in this worker** rebuilds the client in the single process that served the page. It is meant
for testing a new DSN, not as a substitute for a restart.

## Verifying it works

From the settings page use **Send test event**, or from the command line:

```bash
indico sentry info      # what this process is running
indico sentry test      # send a test message
indico sentry test --crash   # send a real exception with a stack trace
```

## How it interacts with Indico core

Core calls `sentry_sdk.init()` in `make_app()` before plugins are loaded. This plugin re-initialises
the SDK on the `app_created` signal, which replaces the global client and closes the old one. If you
remove the plugin, core's client stays in charge and keeps using `indico.conf`.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
```

The tests under `tests/` cover the hooks and helpers and do not need a database.

## License

MIT

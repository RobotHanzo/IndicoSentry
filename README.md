# Indico Sentry Plugin

Sends errors and performance data from [Indico](https://getindico.io) to [Sentry](https://sentry.io).

Indico already has basic Sentry support built in: if you set `SENTRY_DSN` in `indico.conf`, errors are
reported through the Flask, logging and Redis integrations. This plugin replaces that client with a
richer one and moves the configuration into the admin area.

## What it adds

| | Indico core | This plugin |
|---|---|---|
| Configuration | `indico.conf` only | Admin UI, with `indico.conf` as fallback |
| Scope | Server only | Server + browser, separate projects |
| Integrations | Flask, logging, Redis, pure_eval | + Celery, + SQLAlchemy (optional) |
| Performance tracing | – | Per-endpoint sample rates |
| Profiling | – | Yes |
| Error filtering | – | By exception, logger or endpoint |
| Data scrubbing | SDK defaults | Configurable field list, query strings, breadcrumbs |
| Indico context | user, endpoint, RH class | + event, category, contribution, registration |
| Browser errors | – | Sentry's JavaScript SDK, with a DSN of its own |
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

The defaults also silence WeasyPrint (`weasyprint*`), which warns once per glyph the installed fonts
lack, and the font library underneath it (`fontTools*`), so a single PDF with text the fonts cannot
render does not turn into hundreds of events.

### Privacy

- **Send personal data** off → no user, cookies, request body or auth headers leave the server.
- **Send user email and name** off → only the numeric user id is sent, so issues can still be counted
  per user.
- **Scrubbed fields** — any field whose name *contains* one of these fragments is replaced with
  `[Filtered]`, in the request body, cookies, headers, query string, breadcrumbs, tags and extras.

### Frontend (browser)

The Python SDK only ever sees the server half of a page. Turn on **Report browser errors** and give
it a **Browser DSN** to also load [Sentry's JavaScript SDK](https://docs.sentry.io/platforms/javascript/)
on every Indico page.

Use a *separate Sentry project* from the server one. The browser DSN is public — it ends up in the
HTML of every page, so anyone can send events to it — and browser errors are far noisier than server
errors, mostly thanks to extensions and flaky connections. Mixing the two makes the backend project
much less useful.

The SDK is loaded from Sentry's CDN, version-pinned and verified with a SHA-384 subresource integrity
hash. Only the smallest bundle that covers what you enabled is used, so the plain error-reporting
setup costs visitors ~90 kB and tracing or replay is only downloaded if you asked for it. Point
**Custom SDK URL** at your own copy if you would rather not use the CDN.

| Setting | Notes |
|---|---|
| `Report browser errors` | Master switch; nothing is injected while it is off |
| `Browser DSN` | Separate Sentry project, see above |
| `Browser environment` / `Browser release` | Default to the server-side values |
| `Browser trace sample rate` | Page-load and navigation tracing |
| `Session replay sample rate` | Fraction of sessions recorded; text masked, media blocked |
| `Replay on error sample rate` | Additional replays for sessions that hit an error |
| `Link browser and server traces` | Emits `sentry-trace` / `baggage` meta tags |
| `Trace propagation targets` | Where browser requests may carry trace headers, default same-origin |
| `Ignored browser errors` / `Denied script URLs` | Filtering, defaults cover the usual browser noise |

The privacy settings are shared with the server side: turning **Send personal data** off also stops
the browser SDK from attaching the user, and **Send user email and name** decides whether it sends
more than the numeric user id. Session replays are always masked.

The SDK is loaded with `defer`, so errors that happen before it is up are held back and reported once
it is. They are marked as unhandled, like any other uncaught error, and tagged
`indico_sentry.buffered`. If Indico's own scripts did not load (there is no jQuery on the page, as
happens with crawlers that skip subresources), every inline script on the page fails on its own and
those failures say nothing about a bug. They are then reported as a single
`Indico page bundles did not load` warning, with the first error attached.

None of the frontend settings need a restart — the snippet is rebuilt from the settings on every
page render.

Two things are not injected on purpose: pages of an *offline event copy* (a static site must work
without a network), and anything while the master switch is off.

#### Content-Security-Policy

If the instance runs with `CSP_ENABLED`, the plugin adds the SDK host to `script-src` by itself
through the `get_csp_script_sources` signal. Sending the events is a cross-origin request, so if you
set a `connect-src` in `CSP_DIRECTIVES` you have to allow the Sentry ingest host there yourself:

```python
CSP_DIRECTIVES = {
    "connect-src 'self' https://o0.ingest.sentry.io",
    # only needed for session replay, which records in a web worker
    "worker-src 'self' blob:",
}
```

The plugin settings page shows the exact host to use.

## Which settings need a restart?

Sentry is configured once per worker process, so anything that goes into the client itself needs a
restart of Indico and the Celery workers:

`dsn`, `environment`, `release`, `server_name`, `logging_level`, `max_breadcrumbs`,
`attach_stacktrace`, and the integration toggles.

Everything else — sample rates, filtering, scrubbing, privacy, context and all of the frontend
options — is evaluated per event or per page and takes effect within 30 seconds (the settings cache
TTL). The settings page tells you which saved settings the current worker has not picked up yet.

**Reload in this worker** rebuilds the client in the single process that served the page. It is meant
for testing a new DSN, not as a substitute for a restart.

## Upgrading

The default filter lists only apply until the settings form has been saved for the first time. From
then on the lists stored in the database win, so new defaults never reach an existing installation by
themselves. After upgrading, add what is missing on the plugin page. Added in 1.1.1:

| List | New entries |
|---|---|
| Ignored browser errors | `Network Error`, `Java object is gone`, `window.webkit.messageHandlers`, `"@context"].toLowerCase` |
| Denied script URLs | `iabjs://` |
| Ignored loggers | `weasyprint*`, `fontTools*` |

These cover Axios' wording for a dropped connection (what Indico's own requests use), scripts injected
by the Instagram and Facebook in-app browsers, and Safari's JSON-LD reader tripping over the array
Indico's category pages emit.

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

Reading the plugin settings is a database query, and `make_app()` complains about a populated
connection pool right after `app_created` (uWSGI forks its workers afterwards; with `DEBUG = True`
Indico refuses to start). The plugin therefore returns its connection and empties the pool again
before core looks.

The browser snippet goes in through the `html-head` template hook, so it is on every page that uses
Indico's standard layout. Indico rewrites the inline `<script>` to carry the page's CSP nonce.
Browser support for the CSP `script-src` signal needs Indico 3.3.10 or newer; on older versions the
plugin simply skips it, and there is no CSP to satisfy anyway.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
```

The tests under `tests/` cover the hooks and helpers and do not need a database.

## License

MIT

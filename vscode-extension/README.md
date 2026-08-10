# TickHouse Query (VS Code extension)

Run q queries against your TickHouse control plane straight from the editor.
This is a thin client over the **same control-api backend** that powers the
web app's Query workspace (`web-ui/src/pages/Query.jsx`) — same endpoints
(`/auth/login`, `/query/targets`, `/query/run`, `/query/nl2q`), same
read-only guard, same row limits. Nothing new runs against your cluster;
this just gives you another front door onto it.

## What it does

- **`.q` / `.k` files** get basic syntax highlighting (keywords, built-ins,
  symbols, comments, temporal literals).
- **Run a query** (`Ctrl+Enter` / `Cmd+Enter`, or the ▶ button, or the
  command palette) — runs the current selection, or the whole file if
  nothing's selected — against whichever target you've picked, and shows
  the result grid in a panel beside the editor.
- **Generate q from plain English** — same `/query/nl2q` endpoint the web
  workspace's "Describe it →" box uses (an LLM if one's configured on the
  server, with the same behavior either way).
- **Target picker** in the status bar — switch between the gateway, a
  tickerplant's live buffer, or a specific RDB shard, exactly like the
  target chips in the web UI.

## Setup

1. Point it at your control-api (defaults to `http://localhost:8000`, i.e.
   the port docker-compose publishes it on):
   - Command palette → **TickHouse: Set control-api URL…**, or set
     `tickhouse.apiUrl` in Settings.
2. Command palette → **TickHouse: Log In** (same credentials as the web
   app — `.env`'s `ADMIN_USER` / `ADMIN_PASSWORD_HASH`, default
   `admin@demo-bank.local` / `changeme` on a fresh local demo). The token
   is stored in VS Code's secret storage (OS keychain), not in settings.
3. Command palette → **TickHouse: Select Target…** to pick gateway / an
   rdb-\* / a tp-\*. Defaults to `gateway` (`tickhouse.defaultTarget`).
4. **TickHouse: New Query File**, write some q, `Ctrl`/`Cmd`+`Enter`.

A 401 mid-command (expired/missing session) prompts you to log in right
there instead of just failing.

## Commands

| Command | What |
|---|---|
| `TickHouse: Log In` / `Log Out` | control-api session (JWT), stored in SecretStorage |
| `TickHouse: Select Target…` | choose the active query target |
| `TickHouse: Run Query` | run selection (or whole file) against the active target |
| `TickHouse: Generate q from Plain English…` | `/query/nl2q` → inserted into the editor |
| `TickHouse: New Query File` | opens an untitled `.q` scratch file |
| `TickHouse: Set control-api URL…` | change `tickhouse.apiUrl` |

## Settings

| Setting | Default | |
|---|---|---|
| `tickhouse.apiUrl` | `http://localhost:8000` | control-api base URL |
| `tickhouse.defaultTarget` | `gateway` | target used until you pick one |
| `tickhouse.rowLimit` | `1000` | row limit sent with each query |

## Known limitations (v0.1)

- Read-only by default, same as the web workspace — this extension never
  sends `allow_write: true`; there's no UI for it here. Use the web app
  if a deployment has `QUERY_ALLOW_WRITE` on and you need writes.
- Only single-target runs (no multi-target scatter-gather/federation UI
  like the web app's target chips + `_target` column).
- Syntax highlighting covers `/`-prefixed line comments only (a `/` must
  start the line) — inline trailing comments and `/ ... \` block comments
  aren't tokenized, since disambiguating a leading `/` from the divide
  operator needs more than TextMate regex.
- No export (Parquet/S3/ADLS) — use the web app's Query workspace for that.

## Developing

```sh
cd vscode-extension
npm install
npm run compile   # or: npm run watch
```

Then `F5` in VS Code (with this folder open) to launch an Extension
Development Host with it loaded.

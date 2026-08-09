# Revert Google Workspace setup from hosted MCP back to local stdio servers

> In the context of the toolkit's Google setup, facing hosted Google MCP servers
> (`*mcp.googleapis.com`) that return **"The caller does not have permission"** on
> every `tools/call` for our accounts, I decided to **revert to the local stdio
> community servers** (Gmail/Drive/Calendar), driven by the package's own `auth`
> subcommand and a Desktop OAuth client the operator points the setup at by path,
> to achieve **actually-working** Google tools, accepting a return to three
> per-app packages, per-app browser consent, and the Node-24 / slow-boot tax.

## Context

[AgDR-0001](AgDR-0001-official-google-workspace-mcp.md) unified the Google setup
onto Google's **official hosted** Workspace MCP servers — `gmailmcp`, `drivemcp`,
`calendarmcp`.googleapis.com — behind one user-created OAuth client, registered
with `claude mcp add --transport http`. The promise was unified auth + provider
reliability. In practice the hosted servers **do not work** for our accounts:

- `initialize` and `tools/list` succeed, but **every `tools/call` returns
  `"The caller does not have permission"`** — a terse, detail-stripped
  `PERMISSION_DENIED` from Google's hosted service.
- Proven unfixable from the client: the same OAuth tokens return **200 OK**
  against the standard product REST APIs (gmail/drive/calendar.googleapis.com),
  the `serviceusage.services.use` / quota-project check passes, scopes are
  correct — and **enabling the `*mcp.googleapis.com` APIs in the GCP project did
  not change the result**. It fails identically on two separate accounts
  (`minaalfy8`, `minaalfykamel`), so it is a Google-side gating/preview issue,
  not our misconfiguration.
- The hosted Gmail scope set also included `gmail.metadata`, which makes Gmail
  **search** fail with *"Metadata scope does not support 'q' parameter"* even when
  the server otherwise works.

Meanwhile the **local community servers work**: pointed at the same accounts'
tokens they return real data (Gmail 37 labels, Drive folder listings, Calendar
events). They call the standard product APIs, which are healthy.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **Keep hosted, wait for Google** | one OAuth client; provider-hosted; official | non-functional today; opaque error; no client-side fix; unknown timeline |
| **Local stdio community servers** (chosen) | actually works now; standard product APIs; Gmail scope drops `gmail.metadata` so search works | three per-app packages; per-app browser consent; Node-24 fetch shim; ~10s boot needs `MCP_TIMEOUT`; Testing-mode 7-day token expiry |
| **Direct REST-only (no MCP)** | no server to run | no MCP tools surfaced; every task hand-rolled |

## Decision

Chosen: **local stdio community servers**, because they are the only option that
produces working Google MCP tools today. `setup-mcp.py`'s Google flow now
(`auth_kind: "local_oauth"`, `launcher: "npx"`):

1. Reads a **Desktop** OAuth client from a JSON path — interactively or via the
   new **`--client-secret <path>`** flag (plus `--handle` / `--apps` for a fully
   non-interactive per-account run). The default now also scans the repo root for
   a dropped `*client_secret*.json` — matching the `.gitignore` glob, which omits
   the leading underscore so Google Console's own download filename is covered.
2. Per selected app: writes `state/google/<slug>/<app>/gcp-oauth.keys.json`, runs
   the package's **`auth` subcommand** (browser consent → per-app token file),
   then registers the local stdio server with `claude mcp add … -- npx -y <pkg>`.
3. Ensures the Node-24 `node-fetch`→undici preload shim exists and raises
   `MCP_TIMEOUT` in `~/.claude/settings.json` so the slow-booting servers connect.

Per-app specifics honoured (source-verified): Gmail requests
`gmail.modify`+`gmail.settings.basic` (no metadata); loopback ports Gmail `:3000`,
Drive `:3000-3004`, Calendar `:3500-3505`; the auth subprocess runs with a cleaned
env (strips Drive's `GOOGLE_APPLICATION_CREDENTIALS`/`*_ACCESS_TOKEN` footguns and
`GOOGLE_ACCOUNT_MODE`) and `cwd` = the app state dir (neutralises Gmail's
cwd→`GMAIL_OAUTH_PATH` copy).

## Consequences

- Google tools work again; Gmail search works (no `gmail.metadata`).
- Setup is idempotent + scriptable per account via `--client-secret`.
- **Operational tax returns**: three packages, per-app browser consent, the
  Node-24 shim, and ~10s boots (mitigated by auto-setting `MCP_TIMEOUT`).
- **Testing-mode consent screens expire refresh tokens ~weekly** — publish the
  consent screen to Production for durable tokens (the setup now prints this).
- AgDR-0001 is superseded for the "hosted vs local" call; its fragmentation
  critique still stands and is the reason this remains three servers.

## Artifacts

- `setup-mcp.py` — `SERVICES["google"]` + `_setup_local_oauth` + local helpers
  (`_ensure_undici_shim`, `_ensure_mcp_timeout`, `_write_gcp_oauth_keys`,
  `_run_google_app_auth`) + `--client-secret/--handle/--apps` flags.
- Branch `feature/GH-4-google-local-stdio-setup`.

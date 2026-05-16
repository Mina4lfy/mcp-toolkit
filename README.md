# mcp-toolkit

Plug-and-play setup for Google MCP servers (Gmail / Calendar / Drive) inside Claude Code. One Python entry script, three thin wrappers, three vendored MCP-server repos as git submodules. All OAuth keys + token caches stay under `./state/` (gitignored) so the toolkit can travel with you between machines without leaking credentials.

## What this gives you

Three Claude Code MCP servers — each titled after the Google account that owns them, so you can have multiple accounts registered side-by-side (for example `minaalfykamel@gmail.com - Gmail` and `minaalfy8@gmail.com - Gmail` can co-exist).

| Service | Vendor (cloned in `vendor/`) | Default scopes |
| --- | --- | --- |
| Gmail | [`@gongrzhe/server-gmail-autoauth-mcp`](https://github.com/GongRzhe/Gmail-MCP-Server) | `gmail.modify`, `gmail.settings.basic` |
| Google Calendar | [`@cocal/google-calendar-mcp`](https://github.com/nspady/google-calendar-mcp) | `calendar` |
| Google Drive | [`@piotr-agier/google-drive-mcp`](https://github.com/piotr-agier/google-drive-mcp) | `drive` (full read+write) |

## Prerequisites

- `claude` CLI on PATH (the Claude Code binary).
- `uv` on PATH (the Python script uses PEP-723 inline metadata with `uv run --script`; auto-installs deps in an ephemeral venv).
- `npx` on PATH (Node.js 18+).
- A Google account and access to Google Cloud Console.
- A desktop environment with a default browser — the OAuth flows open a browser tab.

## First-time setup, per service

```bash
# from the toolkit root
./bin/setup-google-gmail.sh
./bin/setup-google-calendar.sh
./bin/setup-google-drive.sh
```

Each command walks you through:

1. **Cloud Console steps** — exact URLs to enable the API, configure the consent screen, add your account as a Test User, and create an OAuth 2.0 Desktop Client ID. Pause for Enter once the JSON is downloaded.
2. **Credentials JSON path prompt** — defaults to the newest `~/Downloads/client_secret_*.json`.
3. **OAuth flow** — delegated to the vendor's own `auth` subcommand (each vendor knows how to write its own token-file format). A browser opens; you sign in and approve the scopes you set on the consent screen.
4. **Connection test** — one API call against the service's identity endpoint (`users.getProfile` / `calendars.get(primary)` / `about.get(user)`). Auto-detects the connected email. Best-effort: if the vendor uses a non-standard token-file format, the test is skipped with a warning.
5. **Title prompt** — defaults to `{email} - {Service Name}`, e.g. `minaalfykamel@gmail.com - Gmail`. You can override.
6. **Claude Code registration** — `claude mcp add --scope user "<title>" --env <CREDS>=... --env <TOKEN>=... -- npx -y <vendor>`.

After the setup completes, restart your Claude Code session for the tools to load.

## Status / health-check

```bash
./setup-google-mcp.py doctor
```

Prints `claude mcp list` plus the per-service local state under `./state/`.

## Re-running after token expiry

In OAuth-consent **Testing** publishing-status (the default for personal use), Google expires refresh tokens after 7 days. When this happens, re-run the setup command — pick the same credentials JSON and the same title; the new tokens overwrite the old ones in `./state/<service>/<slug>/`.

## Layout

```text
mcp-toolkit/
├─ README.md
├─ setup-google-mcp.py        # main script (uv-runnable, PEP-723 deps)
├─ bin/
│  ├─ setup-google-gmail.sh
│  ├─ setup-google-calendar.sh
│  └─ setup-google-drive.sh
├─ vendor/                    # git submodules — upstream MCP server code
│  ├─ gmail-mcp/              → GongRzhe/Gmail-MCP-Server
│  ├─ google-calendar-mcp/    → nspady/google-calendar-mcp
│  └─ google-drive-mcp/       → piotr-agier/google-drive-mcp
└─ state/                     # OAuth keys + token caches per <service>/<slug> — gitignored
```

## Cloning the toolkit on a new machine

```bash
git clone <toolkit-repo-url> mcp-toolkit
cd mcp-toolkit
git submodule update --init --recursive
./bin/setup-google-gmail.sh        # (or -calendar / -drive)
```

The `state/` directory does NOT come along — that's intentional (no leaked credentials). Re-do the OAuth flow on each new machine.

## Removing a server

```bash
claude mcp remove "<title>"     # the title you registered with
rm -rf state/<service>/<slug>   # forget the OAuth state too
```

## Why three vendors instead of one all-in-one?

Each Google service has its own community-maintained MCP server with deep coverage for that service's API (drafts, multi-tenant ACLs, custom Sheets formulas, etc.). One all-in-one package (e.g. `aibus-goo-mcp`) trades depth for breadth. This toolkit gives you each service's specialised vendor + a uniform setup harness.

## Adding a fourth Google service later

Edit `setup-google-mcp.py` and add an entry to the `SERVICES` dict — the rest of the script is service-generic. Vendor the upstream repo as a git submodule under `vendor/` and you're done.

## Anti-fabrication note

Each vendor's requested scopes are pulled directly from its source code, verified at clone-time:

- Gmail: `vendor/gmail-mcp/src/index.ts:154-155`
- Calendar: `vendor/google-calendar-mcp/src/auth/server.ts:65`
- Drive: `vendor/google-drive-mcp/src/auth/scopes.ts:6-8`

If a vendor changes its requested scopes upstream, the cloned submodule pin stays put until you explicitly update it (`cd vendor/<name> && git pull`). That's intentional — surprise scope changes are an audit problem.

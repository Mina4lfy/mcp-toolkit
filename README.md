# mcp-toolkit

Plug-and-play setup for MCP servers inside Claude Code, across three providers: Google (Gmail / Calendar / Drive), Atlassian (Jira + Confluence Data Center), and Tempo (Jira time-tracking). One Python entry script, per-service wrappers under `bin/`, vendored MCP-server repos as git submodules under `vendor/`. All credentials + token caches stay under `./state/` (gitignored) so the toolkit can travel with you between machines without leaking secrets.

## What this gives you

Up to five Claude Code MCP servers — each titled after the account or host that owns it, so you can have multiple accounts registered side-by-side (for example `minaalfykamel@gmail.com - Gmail` and `minaalfy8@gmail.com - Gmail` can co-exist, or two Jira instances each with their own Atlassian/Tempo entry).

| Service | Vendor (cloned in `vendor/`) | Launcher | Auth | Default scopes / env |
| --- | --- | --- | --- | --- |
| Gmail | [`@gongrzhe/server-gmail-autoauth-mcp`](https://github.com/GongRzhe/Gmail-MCP-Server) | npx | OAuth browser | `gmail.modify`, `gmail.settings.basic` |
| Google Calendar | [`@cocal/google-calendar-mcp`](https://github.com/nspady/google-calendar-mcp) | npx | OAuth browser | `calendar` |
| Google Drive | [`@piotr-agier/google-drive-mcp`](https://github.com/piotr-agier/google-drive-mcp) | npx | OAuth browser | `drive` (full read+write) |
| Atlassian (Jira + Confluence DC) | [`sooperset/mcp-atlassian`](https://github.com/sooperset/mcp-atlassian) | uvx | Jira DC PAT | `JIRA_URL`, `JIRA_PERSONAL_TOKEN`, optional `CONFLUENCE_URL` + `CONFLUENCE_PERSONAL_TOKEN` |
| Tempo (Jira time tracking) | [`tranzact/tempo-filler-mcp-server`](https://github.com/tranzact/tempo-filler-mcp-server) | npx | Jira DC PAT | `TEMPO_BASE_URL`, `TEMPO_PAT`, optional `TEMPO_DEFAULT_HOURS` |

> **Scope note:** the Atlassian + Tempo entries here target **self-hosted Jira Data Center / Server**. They use a Jira-profile Personal Access Token. They do **not** target Atlassian Cloud — for Cloud, register Atlassian's hosted Rovo MCP server directly (no toolkit support needed since there's no local install).

## Prerequisites

- `claude` CLI on PATH (the Claude Code binary).
- `uv` on PATH (the Python script uses PEP-723 inline metadata with `uv run --script`; auto-installs deps in an ephemeral venv). Also provides `uvx` used to launch the Atlassian server.
- `npx` on PATH (Node.js 18+).
- For Google services: a Google account and access to Google Cloud Console; a desktop environment with a default browser (OAuth opens a browser tab).
- For Atlassian / Tempo: access to a self-hosted Jira Data Center / Server instance and the ability to create a Personal Access Token in your Jira profile.

## First-time setup, per service

```bash
# from the toolkit root
./bin/setup-google-gmail.sh
./bin/setup-google-calendar.sh
./bin/setup-google-drive.sh
./bin/setup-atlassian.sh    # Jira + optional Confluence (Data Center)
./bin/setup-tempo.sh        # Tempo time tracking on Jira tasks (Data Center)
```

The script branches on auth flavour. The Google entries follow `oauth_browser`; the Atlassian + Tempo entries follow `api_token`.

### Google services — `oauth_browser` flow

1. **Cloud Console steps** — exact URLs to enable the API, configure the consent screen, add your account as a Test User, and create an OAuth 2.0 Desktop Client ID. Pause for Enter once the JSON is downloaded.
2. **Credentials JSON path prompt** — defaults to the newest `~/Downloads/client_secret_*.json`.
3. **OAuth flow** — delegated to the vendor's own `auth` subcommand (each vendor knows how to write its own token-file format). A browser opens; you sign in and approve the scopes you set on the consent screen.
4. **Connection test** — one API call against the service's identity endpoint (`users.getProfile` / `calendars.get(primary)` / `about.get(user)`). Auto-detects the connected email. Best-effort: if the vendor uses a non-standard token-file format, the test is skipped with a warning.
5. **Title prompt** — defaults to `<email-handle>-<ServiceName>`, e.g. `minaalfy8-Gmail`. You can override.
6. **Claude Code registration** — `claude mcp add --scope user "<title>" --env <CREDS>=... --env <TOKEN>=... -- npx -y <vendor>`.

### Atlassian + Tempo — `api_token` flow

1. **Generate a Jira DC Personal Access Token** — Jira profile → "Personal Access Tokens" → Create token. Copy the token value.
2. **Enter connection details** — the script prompts for each env var (required + optional). PATs are read with `getpass` so they don't echo. Tempo + Atlassian can share the same PAT if they target the same Jira instance.
3. **Env file** — values are written to `state/<service>/<slug>/env` at mode `0600` for record-keeping and later rotation. Re-run the setup with the same title to rotate the PAT.
4. **Title prompt** — defaults to `<host-slug>-<ServiceName>` derived from the URL you entered (e.g. `jira-company-com-Atlassian`).
5. **Claude Code registration** — `claude mcp add --scope user "<title>" --env KEY=VAL ... -- <launcher> <pkg>`. Atlassian uses `uvx mcp-atlassian`; Tempo uses `npx -y @tranzact/tempo-filler-mcp-server`. The echoed registration command has env values redacted to `KEY=***` so PATs don't show up in logs.

After the setup completes, restart your Claude Code session for the tools to load.

## Status / health-check

```bash
./setup-mcp.py doctor
```

Prints `claude mcp list` plus the per-service local state under `./state/`.

## Re-running after token expiry / PAT rotation

- **Google services**: in OAuth-consent **Testing** publishing-status (the default for personal use), Google expires refresh tokens after 7 days. When this happens, re-run the setup command — pick the same credentials JSON and the same title; the new tokens overwrite the old ones in `./state/<service>/<slug>/`.
- **Atlassian / Tempo**: when your Jira PAT expires or you rotate it for security, re-run the setup command with the same title — the new env file overwrites the old one and Claude Code re-registers with the fresh value.

## Layout

```text
mcp-toolkit/
├─ README.md
├─ setup-mcp.py               # main script (uv-runnable, PEP-723 deps)
├─ bin/
│  ├─ setup-google-gmail.sh
│  ├─ setup-google-calendar.sh
│  ├─ setup-google-drive.sh
│  ├─ setup-atlassian.sh
│  └─ setup-tempo.sh
├─ vendor/                    # git submodules — upstream MCP server code
│  ├─ gmail-mcp/              → GongRzhe/Gmail-MCP-Server
│  ├─ google-calendar-mcp/    → nspady/google-calendar-mcp
│  ├─ google-drive-mcp/       → piotr-agier/google-drive-mcp
│  ├─ mcp-atlassian/          → sooperset/mcp-atlassian
│  └─ tempo-filler-mcp-server/→ tranzact/tempo-filler-mcp-server
└─ state/                     # credentials + token caches per <service>/<slug> — gitignored
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

## Why one vendor per service instead of an all-in-one?

Each service has its own community-maintained MCP server with deep coverage for that API (Gmail drafts, Drive ACLs, Sheets formulas, Jira JQL, Confluence pages, Tempo worklog bulk-fill, etc.). An all-in-one package would trade depth for breadth. This toolkit gives you each service's specialised vendor + a uniform setup harness.

## Adding another service later

Edit `setup-mcp.py` and add an entry to the `SERVICES` dict — set `provider`, `launcher` (`npx` or `uvx`), `auth_kind` (`oauth_browser` or `api_token`), and the per-flavour fields the existing entries demonstrate. Vendor the upstream repo as a git submodule under `vendor/`, add a thin `bin/setup-<name>.sh` wrapper, and you're done.

## Anti-fabrication note

Each vendor's requested scopes / env vars are pulled directly from its source code, verified at clone-time:

- **Gmail**: `vendor/gmail-mcp/src/index.ts:154-155`
- **Calendar**: `vendor/google-calendar-mcp/src/auth/server.ts:65`
- **Drive**: `vendor/google-drive-mcp/src/auth/scopes.ts:6-8`
- **Atlassian (sooperset)** @ `d8bc786`:
  - `vendor/mcp-atlassian/src/mcp_atlassian/jira/config.py:180` — `JIRA_PERSONAL_TOKEN` is read here
  - `vendor/mcp-atlassian/src/mcp_atlassian/confluence/config.py:104` — `CONFLUENCE_PERSONAL_TOKEN` is read here
  - `vendor/mcp-atlassian/src/mcp_atlassian/utils/environment.py:109,148` — PAT-env routing for both apps
- **Tempo (tranzact/tempo-filler)** @ `b9db692` (v2.0.2):
  - `vendor/tempo-filler-mcp-server/src/index.ts:51-53` — `TEMPO_BASE_URL` / `TEMPO_PAT` / `TEMPO_DEFAULT_HOURS` are read here

If a vendor changes its requested scopes / env vars upstream, the cloned submodule pin stays put until you explicitly update it (`cd vendor/<name> && git pull`). That's intentional — surprise scope / env changes are an audit problem.

# mcp-toolkit

Plug-and-play setup for MCP servers inside Claude Code, across seven providers: Google (Gmail / Drive / Calendar, via **local stdio community servers** behind one Desktop OAuth client), Atlassian (Jira + Confluence Data Center), Tempo (Jira time-tracking), LinkedIn (jobs / profiles / messaging / engagement / posts), Azure DevOps (boards / repos / pipelines / wikis / search), GitHub (repos / issues / PRs / Actions / code search, via GitHub's official hosted remote server), and GitLab (projects / issues / merge requests / repo / pipelines, SaaS or self-hosted). One Python entry script, per-service wrappers under `bin/`, vendored MCP-server repos as git submodules under `vendor/` (GitHub is provider-hosted and Google runs community packages via npx, so neither has a submodule). All credentials + token caches stay under `./state/` (gitignored) so the toolkit can travel with you between machines without leaking secrets.

## What this gives you

Claude Code MCP servers across seven providers — each titled after the account or host that owns it, so you can have multiple accounts registered side-by-side (for example a `minaalfykamel-Gmail` and a `minaalfy8-Gmail` can co-exist, or two Jira instances each with their own Atlassian/Tempo entry). Google contributes one server per selected app (Gmail / Drive / Calendar) sharing a single OAuth client; the other providers are one server each.

| Service | Vendor (cloned in `vendor/`) | Launcher | Auth | Default scopes / env |
| --- | --- | --- | --- | --- |
| Google Workspace (Gmail / Drive / Calendar) | Local stdio community servers (`@gongrzhe/server-gmail-autoauth-mcp`, `@piotr-agier/google-drive-mcp`, `@cocal/google-calendar-mcp`) via npx | stdio | OAuth 2.0 (one Desktop client per account, per-app browser consent) | Gmail: `gmail.modify`, `gmail.settings.basic` · Drive: `drive`, `drive.file`, `docs`, `sheets`, `slides` · Calendar: `calendar` |
| Atlassian (Jira + Confluence DC) | [`sooperset/mcp-atlassian`](https://github.com/sooperset/mcp-atlassian) | uvx | Jira DC PAT | `JIRA_URL`, `JIRA_PERSONAL_TOKEN`; `CONFLUENCE_URL` + `CONFLUENCE_PERSONAL_TOKEN` (leave blank to derive from Jira — `JIRA_URL` + `/wiki` and the same PAT) |
| Tempo (Jira time tracking) | [`tranzact/tempo-filler-mcp-server`](https://github.com/tranzact/tempo-filler-mcp-server) | npx | Jira DC PAT | `TEMPO_BASE_URL`, `TEMPO_PAT`, optional `TEMPO_DEFAULT_HOURS` |
| LinkedIn | `vendor/linkedin-mcp/` (this toolkit) | uv (local source) | Cookie paste | `LINKEDIN_LI_AT`, `LINKEDIN_JSESSIONID`, optional timezone + working-hours |
| Azure DevOps (Microsoft official) | [`microsoft/azure-devops-mcp`](https://github.com/microsoft/azure-devops-mcp) | npx | PAT (default, validated at setup) / `az login` / Entra browser | positional `<organization>`, flags `--authentication`, `-d`, `--tenant`; PAT modes set `PERSONAL_ACCESS_TOKEN` or `ADO_MCP_AUTH_TOKEN` |
| Azure DevOps (PAT fallback) | [`Tiberriver256/mcp-server-azure-devops`](https://github.com/Tiberriver256/mcp-server-azure-devops) | npx | Azure DevOps PAT | `AZURE_DEVOPS_ORG_URL`, `AZURE_DEVOPS_AUTH_METHOD`, `AZURE_DEVOPS_PAT`, optional `AZURE_DEVOPS_DEFAULT_PROJECT` |
| GitHub (official remote) | [`github/github-mcp-server`](https://github.com/github/github-mcp-server) (GitHub-hosted, no local install) | HTTP transport | GitHub PAT | endpoint `https://api.githubcopilot.com/mcp/`, header `Authorization: Bearer <PAT>` |
| GitLab (SaaS or self-hosted) | [`zereight/gitlab-mcp`](https://github.com/zereight/gitlab-mcp) | npx | GitLab PAT | `GITLAB_PERSONAL_ACCESS_TOKEN`, `GITLAB_API_URL`, optional `GITLAB_PROJECT_ID`, `GITLAB_READ_ONLY_MODE`, `GITLAB_ALLOWED_PROJECT_IDS` |

> **Scope note:** the Atlassian + Tempo entries here target **self-hosted Jira Data Center / Server**. They use a Jira-profile Personal Access Token. They do **not** target Atlassian Cloud — for Cloud, register Atlassian's hosted Rovo MCP server directly (no toolkit support needed since there's no local install).
>
> **Azure DevOps scope:** both ADO entries target Azure DevOps Services (`dev.azure.com/<org>`) and Azure DevOps Server (self-hosted) the same way — auth is what differs. The Microsoft entry defaults to `pat` because the toolkit validates the credential by probing `/_apis/projects` before registering — so if setup says "done", the next tool call won't surprise you with an auth error. `azcli` is similarly validated (toolkit pulls an ADO-scoped token via `az` and probes the same endpoint). `interactive` defers the Entra browser flow to the first tool call (unreliable inside snap-confined editors). The Tiberriver256 entry is a pure-PAT fallback that slots into the same `api_token` flow as Atlassian + Tempo — useful when the Microsoft server's auth chain isn't workable.

## Prerequisites

- The Claude Code CLI. The toolkit finds it automatically, checking in order: `$MCP_TOOLKIT_CLAUDE_BIN`, then `PATH`, then `~/.claude/local/claude`, then the binary bundled inside a VS Code / Cursor / Windsurf Claude Code extension. If Claude Code is installed **only** as an editor extension the CLI is not on `PATH`, and the toolkit picks it up from the extension bundle — no setup needed. To point at a specific build:

  ```bash
  export MCP_TOOLKIT_CLAUDE_BIN="$(ls -d ~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude | tail -1)"
  ```

  Run `./setup-mcp.py doctor` to see which binary it resolved.
- `uv` on PATH (the Python script uses PEP-723 inline metadata with `uv run --script`; auto-installs deps in an ephemeral venv). Also provides `uvx` used to launch the Atlassian server.
- `npx` on PATH (Node.js 18+).
- For Google: a Google account and access to Google Cloud Console (to enable APIs, add scopes, and create ONE OAuth 2.0 client); a desktop environment with a default browser (the `claude mcp login` OAuth step opens a browser tab). `gcloud` is optional (the setup prints a `gcloud services enable …` one-liner, but you can enable the APIs from the Console UI instead).
- For Atlassian / Tempo: access to a self-hosted Jira Data Center / Server instance and the ability to create a Personal Access Token in your Jira profile.

## First-time setup, per service

```bash
# from the toolkit root
./bin/setup-google.sh                     # Google Workspace (Gmail/Drive/Calendar) — pick apps; idempotent (re-run to add/remove/rotate)
./bin/setup-atlassian.sh                  # Jira + Confluence (Confluence derives from Jira if left blank)
./bin/setup-tempo.sh                      # Tempo time tracking on Jira tasks (Data Center)
./bin/setup-azure-devops.sh               # Microsoft official @azure-devops/mcp
./bin/setup-azure-devops-tiberriver.sh    # Tiberriver256 community PAT fallback
./bin/setup-github.sh                     # GitHub official remote server (HTTP + PAT)
./bin/setup-gitlab.sh                     # GitLab via zereight/gitlab-mcp (PAT, SaaS or self-hosted)
```

The script branches on auth flavour. The Google entry follows `local_oauth` (local stdio servers + one Desktop OAuth client, per-app browser consent); the Atlassian + Tempo + Tiberriver256 + GitLab entries follow `api_token`; the LinkedIn entry follows `cookie_paste`; the Microsoft Azure DevOps entry follows `entra_login`; the GitHub entry follows `remote_http`.

```bash
./bin/setup-linkedin.sh     # LinkedIn (Voyager API + Playwright)
```

### Google Workspace — `local_oauth` flow (local stdio servers, one Desktop OAuth client)

`./bin/setup-google.sh` (or `./setup-mcp.py google`) registers Gmail, Drive, and Calendar as **local stdio MCP servers** — the community packages `@gongrzhe/server-gmail-autoauth-mcp`, `@piotr-agier/google-drive-mcp`, and `@cocal/google-calendar-mcp`, each launched via `npx`. They call the **standard Google product APIs** and authenticate with one user-created **Desktop** OAuth client per account.

> **Why not Google's hosted `*mcp.googleapis.com` remotes?** They return **"The caller does not have permission"** on every `tools/call` for our accounts — an opaque failure that is unfixable client-side and persists across accounts even with the `*mcp` APIs enabled (the same tokens return 200 OK against the product REST APIs). So the toolkit reverted from the hosted unification to the local servers, which work. See `docs/agdr/AgDR-0002-revert-google-to-local-stdio-servers.md`.

Each app is its own server with its own on-disk token, but they **share one Desktop OAuth client** per account. The flow is **idempotent — re-run to add an app, remove one, or rotate the secret** — and can run **non-interactively** via flags:

```
./setup-mcp.py google --client-secret ./minaalfykamel_client_secret_*.json \
                      --handle minaalfykamel --apps gmail,drive,calendar
```

1. **Pick the account, then the apps (STEP 0–1)** — choose **which Google account** to manage (or pass `--handle`); each account is independent (its own OAuth client + its own `<handle>-Gmail` / `<handle>-GoogleDrive` / `<handle>-GoogleCalendar` servers), so several coexist (e.g. `minaalfy8`, `minaalfykamel`). Then a multiselect app-picker (or `--apps gmail,drive,calendar`) computes `add` / `remove` / `keep`.
2. **Cloud Console prep (STEP 2)** — first run prints the steps: create/select a project; enable the **product** APIs (`gmail`, `drive`/`docs`/`sheets`/`slides`, `calendar-json`.googleapis.com); add the servers' scopes to ONE consent screen (+ add yourself as a Test User); create **one OAuth Client ID of type Desktop app** — **required**, because the servers use a loopback OAuth redirect and rely on Google's loopback exemption (a Web client would need every callback port pre-registered). ⚠ While the consent screen is in **Testing**, Google expires refresh tokens after ~7 days — **publish it to Production** for durable tokens. Adding apps later prints only the *incremental* APIs + scopes.
3. **Credentials (STEP 3)** — point the setup at the downloaded **Desktop** client JSON via `--client-secret <path>`, or the prompt (default scans the repo root's `*_client_secret_*.json` and `~/Downloads/`). Client id + secret are written to a per-app `gcp-oauth.keys.json` (created `0600`) and recorded at `state/google/<handle>/env`; re-runs offer to reuse or replace it.
4. **Authorize + register (STEP 4)** — for each added app the setup runs the package's **`auth` subcommand**, which opens your browser for consent and writes the app's token into `state/google/<handle>/<app>/`, then registers the stdio server: `claude mcp add --scope user --env <KEYS_ENV>=… --env <TOKEN_ENV>=… -- npx -y <package>`. It also ensures the Node-24 `node-fetch`→undici preload shim and raises `MCP_TIMEOUT` in `~/.claude/settings.json` (the servers take ~10s to boot). Deselected apps are removed with `claude mcp remove` (their local token deleted too).
5. **Restart** your Claude Code session to load the new tools.

**Removing / rotating**: re-run and deselect an app to remove it (its stdio server + local token are dropped); when the selection is unchanged it offers to **rotate the client secret** (rewrites each `gcp-oauth.keys.json`).

**Troubleshooting:**

- **A server shows `✘ Failed to connect` after a restart** → the ~10s `googleapis` import lost the startup race. Ensure `MCP_TIMEOUT=120000` is in `~/.claude/settings.json` `env` (the setup sets it) and `/mcp reconnect all`.
- **A Drive/Calendar tool hangs on first call** → you have `GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_DRIVE_MCP_ACCESS_TOKEN` / `GOOGLE_ACCOUNT_MODE` exported; those switch the server to a different auth mode and it ignores its token file. Unset them in the shell that launches Claude Code (the setup warns — `claude mcp add --env` can't unset an inherited var).
- **OAuth callback port busy** (`:3000` Gmail/Drive, `:3500-3505` Calendar) → free it before authorizing (a running Drive MCP squats `:3000`).
- **Gmail search errors "Metadata scope does not support 'q'"** → shouldn't occur on this flow (Gmail requests only `gmail.modify` + `gmail.settings.basic`); if it does, re-authorize Gmail.
- **Tokens stop working after ~a week** → the consent screen is still in Testing mode; publish it to Production.

### LinkedIn — `cookie_paste` flow

LinkedIn has no broadly-accessible official API. This toolkit's LinkedIn server uses cookie-based access against the Voyager mobile API (`linkedin-api` Python lib) plus a Playwright-driven browser session for post creation. **This violates LinkedIn's User Agreement §8.2** (no automated access outside their Marketing/Talent Developer Platform); enforcement is account-level (restriction / ban), not legal. The toolkit ships hard caps + working-hours gates + human-shaped random delays on every write tool, but you accept account-risk by using it.

1. **Get the cookies** — log into LinkedIn in your browser normally (with 2FA). Open DevTools → Application/Storage → Cookies → `https://www.linkedin.com`. Copy the Value column for `li_at` (~120-char session cookie) and `JSESSIONID` (looks like `ajax:1234567890123456`).
2. **Cookie + config prompts** — script asks for the two cookie values (read with `getpass`, no echo), optional account label, optional timezone (default `Europe/Berlin`), optional working-hours window (default 09-19).
3. **Connection test** — script subprocess-runs `python -m linkedin_mcp.ping` with the staged env, which calls `get_user_profile()` on Voyager. Prints `Connected as: <your-name>` on success or `AUTH-FAILED` with the actual error.
4. **Title prompt** — defaults to `<account-label-or-name-slug>-LinkedIn`.
5. **Claude Code registration** — `claude mcp add --scope user "<title>" --env LINKEDIN_LI_AT=*** … -- uv run --directory vendor/linkedin-mcp linkedin-mcp`. The registration command's env values are redacted to `KEY=***` so the cookies never appear in logs.

Cookies last ~90 days unless you log out / change password. Re-run `./bin/setup-linkedin.sh` with the same title to rotate.

**Tool surface** (Phase 1 reads + Phase 2 writes):

| Tool | Class | Cap | Notes |
| --- | --- | --- | --- |
| `search_jobs` | search | 200/day | Keywords + location + remote + experience + job-type filters |
| `get_job` | read | 200/day | Full JD for a numeric job_id |
| `get_recommended_jobs` | search | 200/day | Algorithm-driven feed (highest signal) |
| `get_profile` | read | 200/day | By public_id (the `/in/<slug>` part of a profile URL) |
| `search_people` | search | 200/day | People search with geo / company / school filters |
| `get_feed` | read | 200/day | Recent posts from home feed |
| `get_inbox` | read | 200/day | Recent DM conversations |
| `send_connection_request` | connection_request | 20/day, 5/hr, work-hrs | 300-char optional note; LinkedIn weekly cap ~100 |
| `send_message` | message | 40/day, 10/hr, work-hrs | Reply to thread or open new (1st-degree only without InMail) |
| `react_to_post` | react | 50/day, 15/hr, work-hrs | like / praise / empathy / interest / appreciation / entertainment |
| `comment_on_post` | comment | 10/day, 3/hr, work-hrs | Bot-detection most sensitive here — keep comments unique |
| `create_post` | post | 3/day, 1/hr | Text-only via Playwright; images/video do manually |
| `linkedin_whoami` | — | unbounded | Identity ping; use if other tools start failing |
| `linkedin_throttle_status` | — | unbounded | Inspect cap usage live |

### Atlassian + Tempo — `api_token` flow

1. **Generate a Jira DC Personal Access Token** — Jira profile → "Personal Access Tokens" → Create token. Copy the token value.
2. **Enter connection details** — the script prompts for each env var (required + optional). PATs are read with `getpass` so they don't echo. Tempo + Atlassian can share the same PAT if they target the same Jira instance.
3. **Env file** — values are written to `state/<service>/<slug>/env` at mode `0600` for record-keeping and later rotation. Re-run the setup with the same title to rotate the PAT.
4. **Title prompt** — defaults to `<host-slug>-<ServiceName>` derived from the URL you entered (e.g. `jira-company-com-Atlassian`).
5. **Claude Code registration** — `claude mcp add --scope user "<title>" --env KEY=VAL ... -- <launcher> <pkg>`. Atlassian uses `uvx mcp-atlassian`; Tempo uses `npx -y @tranzact/tempo-filler-mcp-server`. The echoed registration command has env values redacted to `KEY=***` so PATs don't show up in logs.

After the setup completes, restart your Claude Code session for the tools to load.

### Azure DevOps (Microsoft official) — `entra_login` flow

`./bin/setup-azure-devops.sh` registers `microsoft/azure-devops-mcp`. The organisation is a positional CLI arg to the vendor (there is no "list organisations" tool — the org is fixed at setup time). The setup script now **validates credentials live** before registering, so when it says "done", `core_list_projects` works on the next session.

What setup will ask you for, in order:

1. **Org name** — the trailing segment after `https://dev.azure.com/`. If you don't remember which orgs you belong to, the script points you at `https://aex.dev.azure.com/`, which lists every Azure DevOps org tied to your Microsoft account with direct links.
2. **Auth method** (default `pat`):
   - **`pat`** — script prompts for your Microsoft email + raw PAT separately, base64-encodes `email:PAT` for you, probes `https://dev.azure.com/<org>/_apis/projects` with the encoded credential, and refuses to register on 401 / 403 / 404. Distinguishes "PAT is bad" from "org name is wrong" from "PAT scope too narrow".
   - **`azcli`** — script checks `az` is installed, runs `az login` if you're not signed in, pulls an ADO-scoped token via `az account get-access-token --resource 499b84ac-...`, and probes the same projects endpoint with that bearer. Same hard-fail on bad creds.
   - **`envvar`** — raw bearer in `ADO_MCP_AUTH_TOKEN`; probed against the projects endpoint.
   - **`interactive`** — Entra browser flow deferred to the first tool call (warned: unreliable inside snap-confined editors; requires `confirm` to proceed).
   - **`env`** — Azure SDK env-credential chain; no validation possible (requires `confirm` to proceed).
3. **Tenant** (optional, UUID) — used by `interactive` / `azcli` for multi-tenant accounts; blank uses `common`.
4. **Domains** (default `all`) — restrict which tool groups load. Space-separate e.g. `repositories work-items`.
5. **Title** — defaults to `<org-slug>-AzureDevOps`.
6. **Registration** — `claude mcp add … -- npx -y @azure-devops/mcp <org> [-d ...] --authentication <method> [--tenant ...]`. Env vars (`PERSONAL_ACCESS_TOKEN` / `ADO_MCP_AUTH_TOKEN`) are set only when the method needs them.

**PAT scopes** — for setup-time verification you need at minimum **`Project and team: Read`**. For day-to-day use, add the scopes for whatever tool groups you've enabled (common starter: `Code: Read & write`, `Work items: Read & write`, `Build: Read`, `Wiki: Read & write`, `Identity: Read`, `Test management: Read`).

### Azure DevOps (Tiberriver256) — PAT fallback via `api_token`

`./bin/setup-azure-devops-tiberriver.sh` registers `Tiberriver256/mcp-server-azure-devops`. Use this on machines where the Microsoft server's Entra/azcli path isn't workable — it follows the same `api_token` flow as Atlassian + Tempo:

1. **Generate an Azure DevOps PAT** — User settings → Personal access tokens → New token. Recommended starter scopes: Code (read), Work Items (read & write), Build (read). Tighten per use-case.
2. **Enter connection details** — `AZURE_DEVOPS_ORG_URL` (e.g. `https://dev.azure.com/<org>`), `AZURE_DEVOPS_AUTH_METHOD` (default `pat`), `AZURE_DEVOPS_PAT` (read with `getpass`), optional `AZURE_DEVOPS_DEFAULT_PROJECT`.
3. **Title prompt** — defaults to `<org-slug>-AzureDevOps`, derived from the ADO URL (the script special-cases `dev.azure.com/<org>` + `<org>.visualstudio.com` paths).
4. **Claude Code registration** — `claude mcp add … --env AZURE_DEVOPS_ORG_URL=… --env AZURE_DEVOPS_PAT=*** -- npx -y @tiberriver256/mcp-server-azure-devops`.

### GitHub (official remote) — `remote_http` flow

`./bin/setup-github.sh` registers GitHub's **official, GitHub-hosted** MCP server at `https://api.githubcopilot.com/mcp/`. Unlike every other entry, there is **no local install and no submodule to vendor** — the server runs on GitHub's side and Claude Code talks to it over the HTTP transport. Auth is a GitHub Personal Access Token sent as an `Authorization: Bearer <PAT>` header.

1. **Create a GitHub PAT** — either type works:
   - **Fine-grained** (recommended): <https://github.com/settings/personal-access-tokens> — pick the repos + per-resource permissions you want (e.g. Contents, Issues, Pull requests, Metadata).
   - **Classic**: <https://github.com/settings/tokens> — the `repo` scope covers most read/write; add `read:org` for org data.

   You choose the scopes yourself at token-creation time — the toolkit does **not** pre-select them. The server auto-hides tools your token can't use, so a narrower token simply exposes fewer tools.
2. **Token prompt** — read with `getpass` (no echo).
3. **Live validation** — the toolkit probes `https://api.github.com/user` with the PAT before registering, so a green "done" means the token is real (and it auto-detects your login for the title default). Hard-fails on 401/403.
4. **Title prompt** — defaults to `<login>-GitHub` (e.g. `minaalfy-GitHub`).
5. **Claude Code registration** — `claude mcp add --transport http --scope user "<title>" "https://api.githubcopilot.com/mcp/" --header "Authorization: Bearer ***"`. The header value is redacted in the echoed command so the PAT never appears in logs.
6. **Post-registration handshake** — the toolkit drives `initialize` + `tools/list` against the remote endpoint (parsing either JSON or SSE responses) and rolls back the registration if the server doesn't answer.

The PAT is also written to `state/github/<slug>/env` (mode `0600`) for rotation — re-run the setup with the same title to rotate. No Copilot subscription is required for the core GitHub tools.

### GitLab (zereight/gitlab-mcp) — PAT via `api_token`

`./bin/setup-gitlab.sh` registers [`zereight/gitlab-mcp`](https://github.com/zereight/gitlab-mcp) (vendored under `vendor/gitlab-mcp`, launched via the `@zereight/mcp-gitlab` npx package). It follows the same `api_token` flow as Atlassian + Tempo + Tiberriver256, and works against **gitlab.com (SaaS)** and **self-hosted GitLab** — the only difference is the `GITLAB_API_URL` you enter.

1. **Create a GitLab PAT** — avatar → Edit profile → Access tokens → Personal access tokens (SaaS: <https://gitlab.com/-/user_settings/personal_access_tokens>; self-hosted: `<your-instance>/-/user_settings/personal_access_tokens`). Pick scopes at creation time — `read_api` for read-only or `api` for full read+write. The toolkit does **not** pre-select them.
2. **Enter connection details** — `GITLAB_PERSONAL_ACCESS_TOKEN` (read with `getpass`), `GITLAB_API_URL` (default `https://gitlab.com/api/v4`; for self-hosted use `https://gitlab.company.com/api/v4`), and optional `GITLAB_PROJECT_ID` (default project as numeric ID or `group/project` path), `GITLAB_READ_ONLY_MODE` (enter `true` to force read-only regardless of token scopes), `GITLAB_ALLOWED_PROJECT_IDS` (comma-separated allowlist).
3. **Title prompt** — defaults to `<host-slug>-GitLab`, derived from `GITLAB_API_URL` (e.g. `gitlab-com-GitLab`).
4. **Claude Code registration** — `claude mcp add … --env GITLAB_PERSONAL_ACCESS_TOKEN=*** --env GITLAB_API_URL=… -- npx -y @zereight/mcp-gitlab`. Empty optional vars are omitted, not passed as blank.
5. **Post-registration handshake** — the toolkit spawns the server and drives `initialize` + `tools/list` over stdio, rolling back the registration if it doesn't answer.

The PAT is written to `state/gitlab/<slug>/env` (mode `0600`) for rotation — re-run the setup with the same title to rotate.

## Status / health-check

```bash
./setup-mcp.py doctor
```

Prints `claude mcp list` plus the per-service local state under `./state/`.

## Re-running after token expiry / PAT rotation

- **Google**: OAuth tokens are managed by Claude Code (not this toolkit) since the servers are OAuth remotes — when a token expires or is rejected, Claude Code flags the server in `/mcp`; just re-authorize with `claude mcp login "<title>"`. (In OAuth-consent **Testing** publishing-status, Google expires refresh tokens after 7 days.) To rotate the **client secret**, re-run `./bin/setup-google.sh` and choose the rotate option. To add/remove apps, re-run and change the selection.
- **Atlassian / Tempo / Azure DevOps (Tiberriver256) / GitHub / GitLab**: when your PAT expires or you rotate it for security, re-run the setup command with the same title — the new env file overwrites the old one and Claude Code re-registers with the fresh value.
- **Azure DevOps (Microsoft, `interactive` / `azcli`)**: nothing to rotate in this toolkit — the Entra session lives in the vendor's cache (interactive) or your `az` CLI session. For `pat` / `envvar`, re-run the setup with the same title to refresh the env value.

## Layout

```text
mcp-toolkit/
├─ README.md
├─ setup-mcp.py               # main script (uv-runnable, PEP-723 deps)
├─ bin/
│  ├─ setup-google.sh               # Google Workspace (local stdio community servers via npx) — pick apps, one Desktop OAuth client
│  ├─ setup-atlassian.sh
│  ├─ setup-tempo.sh
│  ├─ setup-linkedin.sh
│  ├─ setup-azure-devops.sh
│  ├─ setup-azure-devops-tiberriver.sh
│  ├─ setup-github.sh               # GitHub official remote server (no submodule — hosted)
│  └─ setup-gitlab.sh               # GitLab via zereight/gitlab-mcp (SaaS or self-hosted)
├─ vendor/                          # upstream MCP servers (git submodules) + local linkedin-mcp
│  │                                # (GitHub is provider-hosted; Google runs community pkgs via npx — no submodule either way)
│  ├─ mcp-atlassian/                → sooperset/mcp-atlassian
│  ├─ tempo-filler-mcp-server/      → tranzact/tempo-filler-mcp-server
│  ├─ azure-devops-mcp/             → microsoft/azure-devops-mcp
│  ├─ azure-devops-mcp-tiberriver/  → Tiberriver256/mcp-server-azure-devops
│  ├─ gitlab-mcp/                   → zereight/gitlab-mcp (pinned v2.1.18)
│  └─ linkedin-mcp/                 → local Python pkg in this toolkit (FastMCP + linkedin-api + Playwright)
└─ state/                           # credentials + token caches per `<service>/<slug>` — gitignored
```

## Cloning the toolkit on a new machine

```bash
git clone <toolkit-repo-url> mcp-toolkit
cd mcp-toolkit
git submodule update --init --recursive   # for the vendored (non-hosted) servers
./bin/setup-google.sh                      # Google Workspace — local stdio servers via npx (no submodule)
```

The `state/` directory does NOT come along — that's intentional (no leaked credentials). Re-run the setup and (for Google) re-authorize with `claude mcp login` on each new machine.

## Removing a server

```bash
claude mcp remove "<title>"     # the title you registered with
rm -rf state/<service>/<slug>   # forget the OAuth state too
```

## Why one vendor per service instead of an all-in-one?

For most services, each has its own community-maintained MCP server with deep coverage for that API (Jira JQL, Confluence pages, Tempo worklog bulk-fill, GitLab MRs, etc.). An all-in-one package would trade depth for breadth. This toolkit gives you each service's specialised vendor + a uniform setup harness.

**Google is the exception.** It uses three community vendors (`@gongrzhe` Gmail, `@piotr-agier` Drive, `@cocal` Calendar) as **local stdio servers behind one Desktop OAuth client** per account. [AgDR-0001](docs/agdr/AgDR-0001-official-google-workspace-mcp.md) briefly unified Google onto its official **hosted** Workspace MCP (one client, provider-hosted) — but those endpoints returned *"The caller does not have permission"* on every tool call for our accounts (unfixable client-side), so [AgDR-0002](docs/agdr/AgDR-0002-revert-google-to-local-stdio-servers.md) **reverted to the local servers**, which call the standard product APIs and work. The old fragmentation tax (per-app packages, Node-24 `node-fetch` breakage, a Drive server squatting `:3000`) is managed by the setup (it ensures the undici shim + raises `MCP_TIMEOUT`).

## Adding another service later

Edit `setup-mcp.py` and add an entry to the `SERVICES` dict — set `provider`, `launcher` (`npx` / `uvx` / `uv_local` / `remote_http`), `auth_kind` (`local_oauth` / `api_token` / `cookie_paste` / `entra_login` / `remote_http`), and the per-flavour fields the existing entries demonstrate. For a locally-run server, vendor the upstream repo as a git submodule under `vendor/` (or, like Google's `npx` community packages, rely on npx resolving them — no submodule). Add a thin `bin/setup-<name>.sh` wrapper, and you're done. (To add more Google apps — Chat, People — just add entries to `SERVICES["google"]["apps"]`; the flow picks them up automatically.)

## Anti-fabrication note

Each vendor's requested scopes / env vars are pulled directly from its source code (or, for provider-hosted servers, the provider's own docs), verified at pin-time:

- **Google Workspace** — official Google-hosted remote MCP servers, no vendored submodule to pin. Endpoints + auth verified against Google's docs; **scopes are dictated by each server's OAuth metadata**, not chosen here, so the setup reads the authoritative set live from `claude mcp login --no-browser` after registration (the hardcoded list is a fallback/printout only):
  - Docs: <https://developers.google.com/workspace/guides/configure-mcp-servers>
  - Endpoints: `https://gmailmcp.googleapis.com/mcp/v1`, `https://drivemcp.googleapis.com/mcp/v1`, `https://calendarmcp.googleapis.com/mcp/v1`
  - Scopes observed live (2026-07): Gmail `mail.google.com/` + `gmail.modify/compose/readonly/metadata`; Drive `drive` + `drive.readonly` + `drive.file`; Calendar `calendar` + `calendar.app.created` + `calendar.events(.readonly/.freebusy/.owned/.owned.readonly/.public.readonly)` + `calendar.readonly`
  - Auth: one user-created OAuth 2.0 client (**Desktop app** is simplest and works via Google's loopback flow; a **Web application** client also works if it registers redirect `http://localhost:33418/callback`), registered via `claude mcp add --transport http --client-id … --client-secret --callback-port 33418`, then completed with `claude mcp login`
- **Atlassian (sooperset)** @ `d8bc786`:
  - `vendor/mcp-atlassian/src/mcp_atlassian/jira/config.py:180` — `JIRA_PERSONAL_TOKEN` is read here
  - `vendor/mcp-atlassian/src/mcp_atlassian/confluence/config.py:104` — `CONFLUENCE_PERSONAL_TOKEN` is read here
  - `vendor/mcp-atlassian/src/mcp_atlassian/utils/environment.py:109,148` — PAT-env routing for both apps
- **Tempo (tranzact/tempo-filler)** @ `b9db692` (v2.0.2):
  - `vendor/tempo-filler-mcp-server/src/index.ts:51-53` — `TEMPO_BASE_URL` / `TEMPO_PAT` / `TEMPO_DEFAULT_HOURS` are read here
- **Azure DevOps (microsoft/azure-devops-mcp)** @ `1cd5d89` (v2.7.0 + 34 commits):
  - `vendor/azure-devops-mcp/src/index.ts:32-39` — `<organization>` positional CLI arg
  - `vendor/azure-devops-mcp/src/index.ts:39-45` — `--domains` flag, default `'all'`
  - `vendor/azure-devops-mcp/src/index.ts:46-52` — `--authentication` flag, choices `interactive` / `azcli` / `env` / `envvar` / `pat`
  - `vendor/azure-devops-mcp/src/index.ts:53-56` — `--tenant` flag
  - `vendor/azure-devops-mcp/src/auth.ts:81-94` — `pat` mode reads `PERSONAL_ACCESS_TOKEN` (base64 `email:token`)
  - `vendor/azure-devops-mcp/src/auth.ts:95-108` — `envvar` mode reads `ADO_MCP_AUTH_TOKEN` (raw bearer)
- **Azure DevOps (Tiberriver256/mcp-server-azure-devops)** @ `7ad868b` (v0.1.45):
  - `vendor/azure-devops-mcp-tiberriver/src/index.ts:55-67` — `AZURE_DEVOPS_ORG_URL` / `AZURE_DEVOPS_AUTH_METHOD` / `AZURE_DEVOPS_PAT` / `AZURE_DEVOPS_DEFAULT_PROJECT` are read here
- **GitHub (github/github-mcp-server)** — GitHub-hosted remote server, no vendored submodule to pin. Endpoint + auth verified against GitHub's docs:
  - Remote URL `https://api.githubcopilot.com/mcp/` and `Authorization: Bearer <PAT>` header — GitHub Docs, "Setting up the GitHub MCP Server" (<https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server>)
  - PAT is validated at setup against `https://api.github.com/user` (REST API identity endpoint) before registration
- **GitLab (zereight/gitlab-mcp)** @ `74a8c83` (v2.1.18):
  - `vendor/gitlab-mcp/config.ts:32` — `GITLAB_PERSONAL_ACCESS_TOKEN` is read here
  - `vendor/gitlab-mcp/config.ts:42` — `GITLAB_READ_ONLY_MODE` is read here
  - `vendor/gitlab-mcp/index.ts:1545` — `GITLAB_API_URL` is read here (default `https://gitlab.com`)
  - `vendor/gitlab-mcp/index.ts:1549` — `GITLAB_PROJECT_ID` is read here
  - `vendor/gitlab-mcp/index.ts:1551` — `GITLAB_ALLOWED_PROJECT_IDS` is read here

If a vendor changes its requested scopes / env vars upstream, the cloned submodule pin stays put until you explicitly update it (`cd vendor/<name> && git pull`). That's intentional — surprise scope / env changes are an audit problem.

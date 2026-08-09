#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
setup-mcp.py — interactive setup for MCP servers.

Usage:
    ./setup-mcp.py google                    # Google Workspace (official remote MCP; Gmail/Drive/Calendar)
    ./setup-mcp.py atlassian-sooperset
    ./setup-mcp.py tempo-filler
    ./setup-mcp.py azure-devops              # Microsoft official, Entra/azcli/PAT
    ./setup-mcp.py azure-devops-tiberriver   # Community PAT fallback
    ./setup-mcp.py github                    # GitHub official remote server (HTTP + PAT)
    ./setup-mcp.py gitlab                     # GitLab via zereight/gitlab-mcp (PAT, SaaS or self-hosted)
    ./setup-mcp.py linkedin
    ./setup-mcp.py doctor                    # list registered MCPs + their state

Or use the wrapper scripts under ./bin/.

Five auth flavours are supported:

  • local_oauth  (Google Workspace — LOCAL stdio community MCP servers)
    An idempotent converge flow: re-running reads existing state and applies only
    the delta (add / remove / rotate) rather than starting from scratch.
    1. Detects existing Google state and shows which apps are already registered.
    2. Multiselect which apps you want (Gmail / Drive / Calendar) — registered ones
       pre-checked; the picked set is the desired state (or pass --apps gmail,drive).
    3. First run only: prints the Cloud Console steps (enable the product APIs, add
       the scopes to ONE consent screen, create ONE **Desktop** OAuth client), then
       reads the client id + secret from the downloaded JSON (--client-secret PATH).
    4. Per app: writes a Desktop gcp-oauth.keys.json, runs the package's `auth`
       subcommand (browser consent → per-app token file), then registers the local
       stdio server via `claude mcp add --scope user --env … -- npx -y <package>`.
       Also sets MCP_TIMEOUT + the Node-24 fetch shim so the slow servers connect.
    5. Removes deselected apps via `claude mcp remove`. Persists state for re-runs.

    (Why local, not Google's hosted *mcp.googleapis.com remotes: those return "The
     caller does not have permission" on every tools/call for our accounts —
     unfixable client-side. The community servers use the standard product APIs,
     which work. Reverses the hosted unification. See AgDR-0002.)

  • api_token  (Atlassian DC, Tempo Server, Azure DevOps via Tiberriver256, GitLab)
    1. Prints where to generate the PAT in your Jira / Azure DevOps profile.
    2. Prompts for each required/optional env var.
    3. Writes `state/<svc>/<slug>/env` (mode 600) for rotation/inspection.
    4. Title default is `<host-slug>-<ServiceName>` (derived from the URL you entered).
    5. Registers via `claude mcp add --scope user --env KEY=VAL ... -- <launcher> <pkg>`.

  • cookie_paste  (LinkedIn — Voyager via session cookies)

  • entra_login  (Azure DevOps via Microsoft @azure-devops/mcp)
    1. Prompts for organisation name + auth method (interactive Entra / azcli / pat / envvar).
    2. If pat/envvar, prompts for the secret env var; otherwise auth happens at first tool call.
    3. Prompts for optional tenant + optional domain restriction.
    4. Registers via `claude mcp add … -- npx -y @azure-devops/mcp <org> [flags]`.

  • remote_http  (GitHub via the official remote MCP server)
    1. Prompts for a GitHub PAT and validates it against api.github.com/user.
    2. Registers via `claude mcp add --transport http <title> <url> --header "Authorization: Bearer <PAT>"`.
    3. Post-registration MCP handshake over HTTP (initialize + tools/list), rolls back on failure.

All persistent state (oauth keys + tokens + env files) lives under ./state/<svc>/<slug>/
so the whole toolkit stays portable. The repo's .gitignore excludes ./state/.
"""

import argparse
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent
STATE_ROOT = REPO_ROOT / "state"
EMAIL_CACHE = STATE_ROOT / ".account_email"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s/]+(/.*)?$")

# Claude Code ships either as a standalone CLI on PATH or bundled inside an
# editor extension, where it is not on PATH at all. Resolve it once (GH-13).
_CLAUDE_BIN = None

_CLAUDE_EXTENSION_GLOBS = (
    ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    ".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    ".vscode-insiders/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    ".cursor/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    ".windsurf/extensions/anthropic.claude-code-*/resources/native-binary/claude",
)

CLAUDE_MISSING_HELP = """  ✗ Claude Code CLI not found.

    This toolkit registers servers by shelling out to `claude mcp ...`, so the
    CLI has to be reachable. Looked in:
      • $MCP_TOOLKIT_CLAUDE_BIN
      • PATH
      • ~/.claude/local/claude
      • ~/{.vscode,.vscode-server,.vscode-insiders,.cursor,.windsurf}/extensions/
            anthropic.claude-code-*/resources/native-binary/claude

    Any one of these fixes it:
      • Install the standalone CLI:
          npm install -g @anthropic-ai/claude-code
      • Already have Claude Code as an editor extension? Point at its binary:
          export MCP_TOOLKIT_CLAUDE_BIN="$(ls -d ~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude | tail -1)"
      • Or symlink that binary onto your PATH."""


def _is_executable_file(path):
    # A directory carries the execute bit too ("traversable"); os.path.isfile
    # over Path.is_file() because it returns False, not raises, on EACCES.
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_claude_bin():
    """Locate the Claude Code CLI. Returns an absolute path, or None."""
    override = os.environ.get("MCP_TOOLKIT_CLAUDE_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        return str(path) if _is_executable_file(path) else None

    on_path = shutil.which("claude")
    if on_path:
        return on_path

    local_install = Path.home() / ".claude" / "local" / "claude"
    if _is_executable_file(local_install):
        return str(local_install)

    for pattern in _CLAUDE_EXTENSION_GLOBS:
        found = [p for p in Path.home().glob(pattern) if _is_executable_file(p)]
        if found:
            # Extension dirs accumulate old releases; take the most recent.
            return str(max(found, key=lambda p: p.stat().st_mtime))
    return None


def _claude_missing_message():
    override = os.environ.get("MCP_TOOLKIT_CLAUDE_BIN", "").strip()
    if override:
        return (f"  ✗ MCP_TOOLKIT_CLAUDE_BIN points at '{override}', which is not an "
                f"executable file.\n\n{CLAUDE_MISSING_HELP}")
    return CLAUDE_MISSING_HELP


def claude_cmd(*args):
    """Build an argv list for a `claude` invocation with the CLI resolved."""
    global _CLAUDE_BIN
    if _CLAUDE_BIN is None:
        _CLAUDE_BIN = find_claude_bin()
    if _CLAUDE_BIN is None:
        raise RuntimeError(_claude_missing_message())
    return [_CLAUDE_BIN, *args]


def require_claude_bin():
    """Preflight so a missing CLI fails before we prompt for a credential."""
    global _CLAUDE_BIN
    if _CLAUDE_BIN is None:
        _CLAUDE_BIN = find_claude_bin()
    if _CLAUDE_BIN:
        return True
    print(_claude_missing_message())
    return False


def load_cached_email():
    if not EMAIL_CACHE.exists():
        return None
    value = EMAIL_CACHE.read_text().strip()
    return value if EMAIL_RE.match(value) else None


def save_cached_email(email):
    EMAIL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    EMAIL_CACHE.write_text(email + "\n")


def host_from_url(url):
    try:
        netloc = urlparse(url).netloc or url
    except Exception:
        netloc = url
    netloc = netloc.split(":")[0]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", netloc).strip("-").lower()
    return slug or "host"


def ado_org_from_url(url):
    """Extract the Azure DevOps organisation slug from an org URL.

    Handles both modern (https://dev.azure.com/<org>) and legacy
    (https://<org>.visualstudio.com) shapes. Returns None if the URL
    doesn't look like Azure DevOps — caller falls back to host_from_url.
    """
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = (p.netloc or "").lower().split(":")[0]
    if host == "dev.azure.com" or host.endswith(".dev.azure.com"):
        first = p.path.strip("/").split("/", 1)[0] if p.path else ""
        return slugify(first) if first else None
    if host.endswith(".visualstudio.com"):
        sub = host[: -len(".visualstudio.com")]
        return slugify(sub) if sub else None
    return None


NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


SERVICES = {
    # ── Google Workspace (LOCAL stdio community servers, local_oauth) ─────
    # One user-created **Desktop** OAuth client + one browser consent PER APP.
    # Each selected Google app runs as its OWN local stdio Node MCP server
    # (community npm package), authenticated by running that package's `auth`
    # subcommand once — it opens the browser, you consent, and it writes a
    # per-app token file into ./state/google/<slug>/<app>/.
    #
    # WHY LOCAL, not Google's hosted *mcp.googleapis.com remotes: the hosted
    # servers return "The caller does not have permission" on every tools/call
    # for our accounts — an unfixable-client-side failure that persists across
    # accounts even with the *mcp APIs enabled. The community servers below
    # call the STANDARD product APIs, which work. This reverses the hosted
    # unification (AgDR-0001 / GH-3). See AgDR-0002.
    "google": {
        "provider": "google",
        "launcher": "npx",              # local stdio Node servers, one per app
        "auth_kind": "local_oauth",     # per-app `auth` subcommand → token file
        "label": "Google Workspace (local stdio)",
        "short": "Google",
        "service_name": "Google",
        # The OAuth client MUST be a **Desktop app** ("installed") — every local
        # server uses a loopback redirect and relies on Google's loopback
        # exemption (any http://localhost:<port> is accepted, no pre-registration).
        "oauth_client_type": "desktop",
        "docs_url": "https://developers.google.com/workspace/guides/create-credentials",
        "consent_url": "https://console.cloud.google.com/apis/credentials/consent",
        "credentials_url": "https://console.cloud.google.com/apis/credentials",
        "apps": {
            "gmail": {
                "service_name": "Gmail",
                "package": "@gongrzhe/server-gmail-autoauth-mcp",
                "keys_env": "GMAIL_OAUTH_PATH",        # → gcp-oauth.keys.json
                "token_env": "GMAIL_CREDENTIALS_PATH", # → token file it writes
                "token_file": "credentials.json",
                "auth_ports": [3000],                  # hardcoded loopback callback
                "apis": [
                    ("Gmail API", "gmail.googleapis.com"),
                ],
                # Scopes the server's OWN `auth` flow requests. Note: NO
                # gmail.metadata (which the hosted server requested and which
                # breaks Gmail search — "Metadata scope does not support 'q'").
                "scopes": [
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://www.googleapis.com/auth/gmail.settings.basic",
                ],
            },
            "drive": {
                "service_name": "GoogleDrive",
                # Also exposes Docs/Sheets/Slides/Calendar tools (120+ Workspace tools).
                "package": "@piotr-agier/google-drive-mcp",
                "keys_env": "GOOGLE_DRIVE_OAUTH_CREDENTIALS",
                "token_env": "GOOGLE_DRIVE_MCP_TOKEN_PATH",
                "token_file": "tokens.json",
                "auth_ports": [3000, 3001, 3002, 3003, 3004],
                "apis": [
                    ("Google Drive API", "drive.googleapis.com"),
                    ("Google Docs API", "docs.googleapis.com"),
                    ("Google Sheets API", "sheets.googleapis.com"),
                    ("Google Slides API", "slides.googleapis.com"),
                ],
                "scopes": [
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly",
                    "https://www.googleapis.com/auth/documents",
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/presentations",
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/userinfo.email",
                    "openid",
                ],
            },
            "calendar": {
                "service_name": "GoogleCalendar",
                "package": "@cocal/google-calendar-mcp",
                "keys_env": "GOOGLE_OAUTH_CREDENTIALS",
                "token_env": "GOOGLE_CALENDAR_MCP_TOKEN_PATH",
                "token_file": "tokens.json",
                "auth_ports": [3500, 3501, 3502, 3503, 3504, 3505],
                "apis": [
                    ("Google Calendar API", "calendar-json.googleapis.com"),
                ],
                "scopes": [
                    "https://www.googleapis.com/auth/calendar",
                ],
            },
        },
    },
    # ── Atlassian Data Center via sooperset/mcp-atlassian (api_token) ─────
    "atlassian-sooperset": {
        "provider": "atlassian",
        "launcher": "uvx",
        "auth_kind": "api_token",
        "label": "Atlassian (Jira + Confluence, Data Center)",
        "short": "Atlassian",
        "service_name": "Atlassian",
        "package": "mcp-atlassian",
        "title_source_env": "JIRA_URL",
        "pat_setup_url": "https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html",
        "scopes_note": (
            "Uses one Jira Data Center Personal Access Token (PAT) with the\n"
            "account's full Jira/Confluence permissions — no per-scope selector.\n"
            "Source pin: vendor/mcp-atlassian @ d8bc786\n"
            "  src/mcp_atlassian/jira/config.py:180          (JIRA_PERSONAL_TOKEN read)\n"
            "  src/mcp_atlassian/confluence/config.py:104    (CONFLUENCE_PERSONAL_TOKEN read)"
        ),
        "env_vars": [
            {
                "name": "JIRA_URL",
                "required": True,
                "validator": "url",
                "description": "Your Jira Data Center base URL (e.g. https://jira.company.com)",
            },
            {
                "name": "JIRA_PERSONAL_TOKEN",
                "required": True,
                "validator": "nonempty",
                "description": "Jira DC PAT (Profile → Personal Access Tokens → Create token)",
                "secret": True,
            },
            {
                "name": "CONFLUENCE_URL",
                "required": False,
                "validator": "url_or_empty",
                "description": "Confluence base URL (e.g. https://confluence.company.com or .../wiki). Leave blank to derive from JIRA_URL (appends /wiki — correct for Atlassian Cloud / single-instance, where Jira + Confluence share a host).",
                "derive_from_env": "JIRA_URL",
                "derive_suffix": "/wiki",
            },
            {
                "name": "CONFLUENCE_PERSONAL_TOKEN",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": "Confluence DC PAT. Leave blank to reuse JIRA_PERSONAL_TOKEN (the same token works when Jira + Confluence share an instance).",
                "secret": True,
                "derive_from_env": "JIRA_PERSONAL_TOKEN",
            },
        ],
    },
    # ── LinkedIn (cookie_paste) — local vendor under vendor/linkedin-mcp ──
    "linkedin": {
        "provider": "linkedin",
        "launcher": "uv_local",
        "auth_kind": "cookie_paste",
        "label": "LinkedIn",
        "short": "LinkedIn",
        "service_name": "LinkedIn",
        "local_pkg_dir": "vendor/linkedin-mcp",
        "local_pkg_console_script": "linkedin-mcp",
        "scopes_note": (
            "Cookie-based access against LinkedIn's Voyager mobile API.\n"
            "Cookies needed: li_at (session cookie, ~120 chars) and JSESSIONID\n"
            "(CSRF cookie, looks like 'ajax:NNNNNNNNNNNNNNNN'). Rotate every\n"
            "~90 days or whenever you log out / change password.\n"
            "Source pin: vendor/linkedin-mcp v0.1.0 (this toolkit)"
        ),
        "env_vars": [
            {
                "name": "LINKEDIN_LI_AT",
                "required": True,
                "validator": "li_at",
                "description": (
                    "li_at cookie value. In Chrome: F12 → Application → Cookies → "
                    "https://www.linkedin.com → li_at → Value. Paste the value only, "
                    "no surrounding quotes."
                ),
                "secret": True,
            },
            {
                "name": "LINKEDIN_JSESSIONID",
                "required": True,
                "validator": "jsessionid",
                "description": (
                    "JSESSIONID cookie value. Same place in DevTools. Typically "
                    "looks like 'ajax:NNNNNNNNNNNNNNNN'. Paste without surrounding quotes."
                ),
                "secret": True,
            },
            {
                "name": "LINKEDIN_ACCOUNT_LABEL",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": (
                    "Free-text label that appears in server logs (e.g. 'minaalfy'). "
                    "Defaults to the title slug if blank."
                ),
            },
            {
                "name": "LINKEDIN_TIMEZONE",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": (
                    "IANA timezone used by throttle/working-hours gate "
                    "(default: Europe/Berlin)."
                ),
            },
            {
                "name": "LINKEDIN_WORKING_HOURS_START",
                "required": False,
                "validator": "int_or_empty",
                "description": "Hour (0-23) engagement actions become allowed (default: 9).",
            },
            {
                "name": "LINKEDIN_WORKING_HOURS_END",
                "required": False,
                "validator": "int_or_empty",
                "description": "Hour (0-23) engagement actions become blocked (default: 19).",
            },
        ],
    },
    # ── Azure DevOps (Microsoft official) via @azure-devops/mcp (entra_login) ──
    "azure-devops": {
        "provider": "azure-devops",
        "launcher": "npx",
        "auth_kind": "entra_login",
        "label": "Azure DevOps (Microsoft official)",
        "short": "AzureDevOps",
        "service_name": "AzureDevOps",
        "npx_package": "@azure-devops/mcp",
        "scopes_note": (
            "Auth options exposed by @azure-devops/mcp:\n"
            "  • interactive (default) — opens an Entra browser flow on first tool call\n"
            "  • azcli                  — uses your local `az login` session (no PAT)\n"
            "  • pat                    — reads PERSONAL_ACCESS_TOKEN env (base64 'email:token')\n"
            "  • envvar                 — reads ADO_MCP_AUTH_TOKEN env (raw bearer token)\n"
            "  • env                    — Azure SDK env-credential chain (advanced)\n"
            "Source pin: vendor/azure-devops-mcp @ 1cd5d89 (v2.7.0 + 34 commits)\n"
            "  src/index.ts:32-39    (organisation positional arg)\n"
            "  src/index.ts:39-45    (--domains, default 'all')\n"
            "  src/index.ts:46-52    (--authentication choices)\n"
            "  src/index.ts:53-56    (--tenant)\n"
            "  src/auth.ts:81-94     (pat → PERSONAL_ACCESS_TOKEN)\n"
            "  src/auth.ts:95-108    (envvar → ADO_MCP_AUTH_TOKEN)\n"
            "  src/auth.ts:109-130   (azcli/env → DefaultAzureCredential)"
        ),
    },
    # ── Azure DevOps (Tiberriver256 PAT fallback) via api_token ─────────────
    "azure-devops-tiberriver": {
        "provider": "azure-devops",
        "launcher": "npx",
        "auth_kind": "api_token",
        "label": "Azure DevOps (Tiberriver256 — PAT fallback)",
        "short": "AzureDevOps",
        "service_name": "AzureDevOps",
        "npx_package": "@tiberriver256/mcp-server-azure-devops",
        "title_source_env": "AZURE_DEVOPS_ORG_URL",
        "title_source_kind": "ado_org_from_url",
        "pat_setup_url": "https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate",
        "scopes_note": (
            "Uses an Azure DevOps Personal Access Token tied to your Microsoft account.\n"
            "Recommended PAT scopes (least-privilege starter): Code (read),\n"
            "Work Items (read & write), Build (read). Tighten per use-case.\n"
            "Source pin: vendor/azure-devops-mcp-tiberriver @ 7ad868b (v0.1.45)\n"
            "  src/index.ts:55-67  (AZURE_DEVOPS_ORG_URL / AUTH_METHOD / PAT / DEFAULT_PROJECT read)"
        ),
        "env_vars": [
            {
                "name": "AZURE_DEVOPS_ORG_URL",
                "required": True,
                "validator": "url",
                "description": "Your Azure DevOps organisation URL (e.g. https://dev.azure.com/<org>)",
            },
            {
                "name": "AZURE_DEVOPS_AUTH_METHOD",
                "required": True,
                "default": "pat",
                "validator": "nonempty",
                "description": "Auth method: 'pat' (recommended) / 'azure-identity' / 'azure-cli'. If you pick a non-pat method, you'll still be prompted for AZURE_DEVOPS_PAT — leave it blank.",
            },
            {
                "name": "AZURE_DEVOPS_PAT",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": "Azure DevOps PAT (User settings → Personal access tokens → New token). Required when AUTH_METHOD=pat; otherwise leave blank.",
                "secret": True,
            },
            {
                "name": "AZURE_DEVOPS_DEFAULT_PROJECT",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": "Optional default project. Saves having to specify the project arg on each tool call.",
            },
        ],
    },
    # ── Tempo (Jira DC time tracking) via tranzact/tempo-filler (api_token) ──
    "tempo-filler": {
        "provider": "tempo",
        "launcher": "npx",
        "auth_kind": "api_token",
        "label": "Tempo (Jira time tracking, Data Center)",
        "short": "Tempo",
        "service_name": "Tempo",
        "npx_package": "@tranzact/tempo-filler-mcp-server",
        "title_source_env": "TEMPO_BASE_URL",
        "pat_setup_url": "https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html",
        "scopes_note": (
            "Uses one Jira DC Personal Access Token (PAT) — the same kind\n"
            "the sooperset Atlassian server uses. The same PAT can power both\n"
            "servers if they target the same Jira instance.\n"
            "Source pin: vendor/tempo-filler-mcp-server @ b9db692 (v2.0.2)\n"
            "  src/index.ts:51-53   (TEMPO_BASE_URL / TEMPO_PAT / TEMPO_DEFAULT_HOURS)"
        ),
        "env_vars": [
            {
                "name": "TEMPO_BASE_URL",
                "required": True,
                "validator": "url",
                "description": "Your Jira instance URL (e.g. https://jira.company.com)",
            },
            {
                "name": "TEMPO_PAT",
                "required": True,
                "validator": "nonempty",
                "description": "Jira DC Personal Access Token (same kind as for Atlassian)",
                "secret": True,
            },
            {
                "name": "TEMPO_DEFAULT_HOURS",
                "required": False,
                "validator": "int_or_empty",
                "description": "Optional default hours per workday (vendor default: 8). Leave blank to use the default.",
            },
        ],
    },
    # ── GitHub (official remote MCP server) via remote_http ────────────────
    "github": {
        "provider": "github",
        "launcher": "remote_http",  # not a stdio launcher — signals HTTP transport
        "auth_kind": "remote_http",
        "label": "GitHub (official remote MCP server)",
        "short": "GitHub",
        "service_name": "GitHub",
        "remote_url": "https://api.githubcopilot.com/mcp/",
        "pat_setup_url": "https://github.com/settings/personal-access-tokens",
        "scopes_note": (
            "GitHub's official server, hosted by GitHub — no local install, no\n"
            "submodule to vendor. Auth is a GitHub Personal Access Token sent as\n"
            "an 'Authorization: Bearer <PAT>' header on the HTTP transport.\n"
            "\n"
            "Token type — either works:\n"
            "  • Fine-grained PAT (recommended): https://github.com/settings/personal-access-tokens\n"
            "      pick the repos + per-resource permissions you want (e.g.\n"
            "      Contents, Issues, Pull requests, Metadata).\n"
            "  • Classic PAT: https://github.com/settings/tokens (the 'repo' scope\n"
            "      covers most read/write; 'read:org' for org data).\n"
            "You choose the scopes at token-creation time on github.com — this\n"
            "toolkit doesn't pre-select them. The server auto-hides tools your\n"
            "token can't use, so a narrower token just means fewer tools.\n"
            "\n"
            "Endpoint: https://api.githubcopilot.com/mcp/ (GitHub's documented\n"
            "remote MCP URL — works with a PAT; no Copilot subscription needed\n"
            "for the core GitHub tools). Source: GitHub docs, set-up-the-github-mcp-server."
        ),
        "env_vars": [
            {
                "name": "GITHUB_PERSONAL_ACCESS_TOKEN",
                "required": True,
                "validator": "nonempty",
                "description": (
                    "GitHub PAT (fine-grained or classic). Created at "
                    "https://github.com/settings/personal-access-tokens — paste the "
                    "raw token value (starts with 'github_pat_' or 'ghp_')."
                ),
                "secret": True,
            },
        ],
    },
    # ── GitLab via zereight/gitlab-mcp (api_token) ──────────────────────────
    "gitlab": {
        "provider": "gitlab",
        "launcher": "npx",
        "auth_kind": "api_token",
        "label": "GitLab (zereight/gitlab-mcp)",
        "short": "GitLab",
        "service_name": "GitLab",
        "npx_package": "@zereight/mcp-gitlab",
        "title_source_env": "GITLAB_API_URL",
        "pat_setup_url": "https://docs.gitlab.com/user/profile/personal_access_tokens/",
        "pat_howto": (
            "How to create a GitLab Personal Access Token:\n"
            "    1. Sign in to your GitLab instance in a browser.\n"
            "    2. SaaS: https://gitlab.com/-/user_settings/personal_access_tokens\n"
            "       Self-hosted: <your-instance>/-/user_settings/personal_access_tokens\n"
            "       (or avatar → Edit profile → Access tokens → Personal access tokens).\n"
            '    3. Name it (e.g. "Claude Code MCP"), set an expiry, and pick scopes:\n'
            "         • read_api  → read-only access (recommended starter)\n"
            "         • api       → full read + write (issues, MRs, repo, pipelines)\n"
            "         • read_repository / write_repository → narrower repo-only access\n"
            "    4. Click 'Create personal access token' and copy it (shown once)."
        ),
        "scopes_note": (
            "Uses one GitLab Personal Access Token. The token's GitLab scopes decide\n"
            "what the server can do — pick 'read_api' for read-only or 'api' for full\n"
            "read+write at token-creation time; this toolkit doesn't pre-select them.\n"
            "Works against gitlab.com (SaaS) and self-hosted GitLab via GITLAB_API_URL.\n"
            "Optional flags expose extra tools / lock the server down — leave blank to\n"
            "accept the vendor defaults (read+write on, no project restriction).\n"
            "Source pin: vendor/gitlab-mcp @ 74a8c83 (v2.1.18)\n"
            "  config.ts:32   (GITLAB_PERSONAL_ACCESS_TOKEN read)\n"
            "  config.ts:42   (GITLAB_READ_ONLY_MODE read)\n"
            "  index.ts:1545  (GITLAB_API_URL, default https://gitlab.com)\n"
            "  index.ts:1549  (GITLAB_PROJECT_ID read)\n"
            "  index.ts:1551  (GITLAB_ALLOWED_PROJECT_IDS read)"
        ),
        "env_vars": [
            {
                "name": "GITLAB_PERSONAL_ACCESS_TOKEN",
                "required": True,
                "validator": "nonempty",
                "description": (
                    "GitLab PAT (User settings → Access tokens). Use a 'read_api' "
                    "token for read-only or 'api' for full read+write."
                ),
                "secret": True,
            },
            {
                "name": "GITLAB_API_URL",
                "required": False,
                "default": "https://gitlab.com/api/v4",
                "validator": "url",
                "description": (
                    "GitLab API base URL. Default https://gitlab.com/api/v4 (SaaS). "
                    "For self-hosted use https://gitlab.company.com/api/v4."
                ),
            },
            {
                "name": "GITLAB_PROJECT_ID",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": (
                    "Optional default project (numeric ID or 'group/project' path). "
                    "Saves passing the project on each tool call. Leave blank to skip."
                ),
            },
            {
                "name": "GITLAB_READ_ONLY_MODE",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": (
                    "Optional safety switch. Enter 'true' to expose only read-only "
                    "tools regardless of the token's scopes. Leave blank for read+write."
                ),
            },
            {
                "name": "GITLAB_ALLOWED_PROJECT_IDS",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": (
                    "Optional comma-separated allowlist of project IDs the server may "
                    "touch (e.g. '42,1001'). Leave blank for no restriction."
                ),
            },
        ],
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# UI helpers
# ──────────────────────────────────────────────────────────────────────────────

def hr(char="─", width=78):
    print(char * width)


def step(n, title):
    hr()
    print(f"  STEP {n} — {title}")
    hr()


def prompt(question, default=None, validator=None, allow_empty=False, secret=False):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        if secret:
            try:
                import getpass
                answer = getpass.getpass(f"  {question}{suffix}: ").strip()
            except Exception:
                answer = input(f"  {question}{suffix}: ").strip()
        else:
            answer = input(f"  {question}{suffix}: ").strip()
        if not answer and default is not None:
            answer = default
        if not answer:
            if allow_empty:
                return ""
            print("    (empty — try again)")
            continue
        if validator:
            ok, msg = validator(answer)
            if not ok:
                print(f"    ✗ {msg}")
                continue
        return answer


def confirm(question, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"  {question} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


# ──────────────────────────────────────────────────────────────────────────────
# Validators (named, used by api_token env-var prompts)
# ──────────────────────────────────────────────────────────────────────────────

def _v_url(value):
    return (URL_RE.match(value) is not None, "expected an http(s) URL")


def _v_url_or_empty(value):
    if not value:
        return True, None
    return _v_url(value)


def _v_nonempty(value):
    return (bool(value.strip()), "must not be empty")


def _v_nonempty_or_empty(value):
    return True, None


def _v_int_or_empty(value):
    if not value:
        return True, None
    return (value.isdigit(), "expected an integer (or leave blank)")


def _v_li_at(value):
    v = value.strip().strip('"').strip("'")
    if len(v) < 80:
        return False, "li_at cookies are usually ~120+ characters; this looks too short"
    if " " in v or "\n" in v:
        return False, "li_at cannot contain spaces or newlines — paste the value only"
    return True, None


def _v_jsessionid(value):
    v = value.strip().strip('"').strip("'")
    if not v.startswith("ajax:"):
        return False, "JSESSIONID usually starts with 'ajax:' — make sure you copied the whole value"
    if " " in v:
        return False, "JSESSIONID cannot contain spaces"
    return True, None


VALIDATORS = {
    "url": _v_url,
    "url_or_empty": _v_url_or_empty,
    "nonempty": _v_nonempty,
    "nonempty_or_empty": _v_nonempty_or_empty,
    "int_or_empty": _v_int_or_empty,
    "li_at": _v_li_at,
    "jsessionid": _v_jsessionid,
}


# ──────────────────────────────────────────────────────────────────────────────
# Credentials & state-dir handling
# ──────────────────────────────────────────────────────────────────────────────

def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def validate_mcp_name(name):
    if not NAME_RE.match(name):
        return False, "claude mcp names accept letters, numbers, hyphens, underscores ONLY (no spaces, no @, no .)"
    return True, None


def make_state_dir(service_key, slug):
    d = STATE_ROOT / service_key / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_env_file(path, pairs):
    lines = []
    for k, v in pairs.items():
        if v == "":
            continue
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Claude Code registration
# ──────────────────────────────────────────────────────────────────────────────

def _build_spawn_command(launcher, package, local_dir=None, console_script=None, extra_args=None):
    """Reproduce the command claude-code will spawn for this MCP server, so we
    can drive it directly with a JSON-RPC handshake instead of trusting that
    `claude mcp add` exit-0 means the server actually starts."""
    if launcher == "npx":
        cmd = ["npx", "-y", package]
    elif launcher == "uvx":
        cmd = ["uvx", package]
    elif launcher == "uv_local":
        if not local_dir:
            raise ValueError("uv_local launcher requires local_dir")
        cmd = ["uv", "run", "--directory", str(local_dir)]
        cmd += [console_script] if console_script else ["python", "-m", package]
    else:
        raise ValueError(f"unknown launcher: {launcher}")
    if extra_args:
        cmd += list(extra_args)
    return cmd


def _mcp_handshake_test(launcher, package, env_pairs, extra_args=None, local_dir=None, console_script=None, timeout=90):
    """Spawn the just-registered MCP server and drive an initialize +
    tools/list JSON-RPC round-trip over stdio. Returns (ok, message, tool_count).

    What this catches:
      • launcher missing on PATH (npx/uvx/uv not installed)
      • vendor package broken or missing after install
      • env vars not propagated through the registration
      • server crashes during initialize
    What it does NOT catch:
      • credential rejection on first real tool call (auth-flow-specific
        pre-registration probes already cover this for the flows that can)
    """
    try:
        cmd = _build_spawn_command(launcher, package, local_dir, console_script, extra_args)
    except ValueError as e:
        return False, str(e), 0

    env = os.environ.copy()
    for k, v in env_pairs.items():
        if v != "":
            env[k] = v

    requests = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{},'
        '"clientInfo":{"name":"setup-mcp.py","version":"0"}}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )

    try:
        result = subprocess.run(
            cmd, input=requests, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        return False, f"server didn't respond within {timeout}s (first-run npm/uvx install can be slow — retry once)", 0
    except FileNotFoundError:
        return False, f"launcher binary not found on PATH: '{cmd[0]}'", 0
    except Exception as e:
        return False, f"failed to spawn server: {e}", 0

    init_ok = False
    server_info = ""
    tools_count = 0
    tools_error = None
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if msg.get("id") == 1 and "result" in msg:
            init_ok = True
            sinfo = msg["result"].get("serverInfo") or {}
            server_info = f"{sinfo.get('name', '?')} v{sinfo.get('version', '?')}"
        elif msg.get("id") == 2 and "result" in msg and "tools" in msg["result"]:
            tools_count = len(msg["result"]["tools"])
        elif msg.get("id") == 2 and "error" in msg:
            tools_error = msg["error"]

    if not init_ok:
        tail = "\n".join((result.stderr or "").splitlines()[-12:])
        return False, (
            f"server didn't complete initialize (exit {result.returncode}).\n"
            f"    stderr tail:\n      " + tail.replace("\n", "\n      ")
        ), 0
    if tools_error:
        return False, f"initialize OK ({server_info}) but tools/list errored: {tools_error}", 0
    return True, f"{server_info} responded — {tools_count} tools exposed", tools_count


def _finalize_with_handshake(title, final_dir, launcher, package, env_pairs, extra_args=None, local_dir=None, console_script=None, step_num=None):
    """Run the post-registration handshake test. On failure, roll back the
    `claude mcp add` so Claude Code doesn't carry a broken handle, and return
    False. State dir is preserved for debugging."""
    if step_num is not None:
        step(step_num, "Smoke test — JSON-RPC round-trip against the registered server")
    print("  Spawning the same command Claude Code will use, sending initialize + tools/list…")
    ok, msg, _ = _mcp_handshake_test(
        launcher=launcher, package=package, env_pairs=env_pairs,
        extra_args=extra_args, local_dir=local_dir, console_script=console_script,
    )
    if ok:
        print(f"  ✓ {msg}")
        return True
    print(f"  ✗ Handshake failed: {msg}")
    print()
    print("  Rolling back the registration so the session won't load a broken server…")
    rm = subprocess.run(
        claude_cmd("mcp", "remove", "--scope", "user", title),
        capture_output=True, text=True,
    )
    if rm.returncode == 0:
        print(f"  ✓ unregistered '{title}'.")
    else:
        print(f"  ⚠ rollback failed (exit {rm.returncode}): {rm.stderr.strip()}")
        print(f"     Run manually: claude mcp remove --scope user {title}")
    print(f"  ⓘ State dir preserved at {final_dir} for debugging.")
    return False


def claude_mcp_add(title, launcher, package, env_pairs, step_num=6, local_dir=None, console_script=None, extra_args=None):
    cmd = claude_cmd("mcp", "add", "--scope", "user", title)
    for k, v in env_pairs.items():
        if v == "":
            continue
        cmd += ["--env", f"{k}={v}"]
    cmd += ["--"]
    try:
        cmd += _build_spawn_command(launcher, package, local_dir, console_script, extra_args)
    except ValueError as e:
        print(f"  ✗ {e}")
        return False
    step(step_num, f"Registering with Claude Code as '{title}'")
    print("  command:")
    redacted = []
    skip_next = False
    for tok in cmd:
        if skip_next:
            if "=" in tok:
                k = tok.split("=", 1)[0]
                redacted.append(f"{k}=***")
            else:
                redacted.append(tok)
            skip_next = False
            continue
        if tok == "--env":
            skip_next = True
        redacted.append(tok)
    print("    " + " ".join(_shellquote(c) for c in redacted))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ✗ claude mcp add failed (exit {result.returncode}).")
        return False
    print(f"  ✓ Registered. Restart your Claude Code session to load tools.")
    return True


def _shellquote(s):
    if re.match(r"^[A-Za-z0-9_@:./=,+\-]+$", s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


# ──────────────────────────────────────────────────────────────────────────────
# Setup — local_oauth branch (Google Workspace, LOCAL stdio community MCP servers)
# ──────────────────────────────────────────────────────────────────────────────

def prompt_multiselect(question, options, preselected=None):
    """Toggle-style multiselect. `options` is a list of (key, label). Returns the
    selected keys in `options` order. Enter accepts the current selection."""
    keys = [k for k, _ in options]
    selected = set(preselected or [])

    def _render():
        for i, (k, label) in enumerate(options, 1):
            mark = "x" if k in selected else " "
            print(f"    {i}. [{mark}] {label}")

    print(f"  {question}")
    _render()
    print("  Type numbers (space/comma separated) to TOGGLE, 'all', 'none',")
    print("  or press Enter to accept the current selection.")
    while True:
        raw = input("  selection: ").strip().lower()
        if raw == "":
            if not selected:
                print("    (nothing selected — pick at least one, or type 'all')")
                continue
            return [k for k in keys if k in selected]
        if raw == "all":
            selected = set(keys)
        elif raw == "none":
            selected = set()
        else:
            toks = [t for t in re.split(r"[\s,]+", raw) if t]
            if not all(t.isdigit() and 1 <= int(t) <= len(options) for t in toks):
                print("    ✗ enter valid option numbers, 'all', 'none', or Enter")
                continue
            for t in toks:
                k = keys[int(t) - 1]
                selected.discard(k) if k in selected else selected.add(k)
        _render()


# --- Google shared-OAuth-client state (one client covers every selected app) ---

def _list_google_handles(service_key):
    """Every registered Google account under state/<service_key>/, newest first.
    Each handle is an INDEPENDENT account (its own OAuth client + its own
    <handle>-Gmail/Drive/Calendar servers) — the toolkit supports several
    side-by-side (e.g. minaalfy8 and minaalfykamel)."""
    root = STATE_ROOT / service_key
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "env").exists()]
    dirs.sort(key=lambda d: (d / "env").stat().st_mtime, reverse=True)
    out = []
    for d in dirs:
        env = _parse_env_file(d / "env")
        out.append(env.get("GOOGLE_HANDLE", d.name))
    return out


def _parse_env_file(path):
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def _load_google_state(service_key, handle):
    """Load the state for ONE specific account (handle), or None if it's new.
    Keyed by the handle's slug — NOT most-recent — so adding a second account
    never converges (overwrites) the first."""
    if not handle:
        return None
    d = STATE_ROOT / service_key / (slugify(handle) or "google")
    if not (d / "env").exists():
        return None
    env = _parse_env_file(d / "env")
    apps = [a for a in env.get("GOOGLE_APPS", "").split(",") if a]
    return {
        "dir": d,
        "handle": env.get("GOOGLE_HANDLE", d.name),
        "client_id": env.get("GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret": env.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "callback_port": env.get("GOOGLE_OAUTH_CALLBACK_PORT", ""),
        "apps": apps,
    }


def _save_google_state(service_key, handle, client_id, client_secret, callback_port, apps):
    slug = slugify(handle) or "google"
    d = make_state_dir(service_key, slug)
    write_env_file(d / "env", {
        "GOOGLE_HANDLE": handle,
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
        "GOOGLE_OAUTH_CALLBACK_PORT": str(callback_port),
        "GOOGLE_APPS": ",".join(apps),
    })
    return d


def _google_union_apis(s, keys):
    seen, out = set(), []
    for k in keys:
        for name, api_id in s["apps"][k]["apis"]:
            if api_id not in seen:
                seen.add(api_id)
                out.append((name, api_id))
    return out


def _google_union_scopes(s, keys):
    seen, out = set(), []
    for k in keys:
        for sc in s["apps"][k]["scopes"]:
            if sc not in seen:
                seen.add(sc)
                out.append(sc)
    return out


def _google_titles(handle, s, keys):
    return {k: f"{handle}-{s['apps'][k]['service_name']}" for k in keys}


def _latest_client_json():
    """Newest OAuth client-secret JSON to offer as the default. Checks the repo
    root (drop a `*client_secret*.json` there — .gitignore covers that glob) AND
    the file Google Cloud Console hands you in ~/Downloads/client_secret_*.json.
    Returns None if neither exists."""
    import glob
    matches = (glob.glob(str(REPO_ROOT / "*client_secret*.json"))
               + glob.glob(str(Path.home() / "Downloads" / "client_secret_*.json")))
    matches = [m for m in matches if os.path.isfile(m)]
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0] if matches else None


def _looks_like_secret(v):
    """True if v is plausibly a Google OAuth client secret (e.g. GOCSPX-…).
    Rejects obvious mis-pastes — a file path, a value with whitespace, or an
    absurdly long string — so we never persist garbage as the client secret
    (which Google then rejects with 'the provided client secret is invalid')."""
    v = (v or "").strip()
    if not v or "/" in v or v.endswith(".json") or any(c.isspace() for c in v):
        return False
    return 8 <= len(v) <= 60


def _read_oauth_client_json(path_str):
    """Parse a downloaded Google OAuth client JSON → (client_id, client_secret).
    The local Google servers need a **Desktop** ('installed') client — they use a
    loopback redirect and rely on Google's loopback exemption (any localhost port).
    A 'web' client is accepted but warned about (its callback ports would need
    pre-registration). Returns (client_id, client_secret) or raises ValueError."""
    path = Path(path_str).expanduser()
    if not path.exists():
        raise ValueError(f"file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        raise ValueError(f"not valid JSON: {e}")
    blk = data.get("installed") or data.get("web")
    if not blk or not blk.get("client_id"):
        raise ValueError("expected a Google OAuth client JSON ('installed'/Desktop or 'web') with a client_id")
    cid, sec = blk["client_id"], blk.get("client_secret", "")
    if not _looks_like_secret(sec):
        raise ValueError("this client JSON has no usable client_secret (or it looks malformed)")
    if "installed" not in data:
        print("    ⚠ this looks like a 'Web application' client — the local servers want a")
        print("      'Desktop app' client (loopback exemption). Recreate as Desktop if auth fails.")
    return cid, sec


def _prompt_oauth_client():
    """Point at the downloaded Desktop OAuth client JSON (preferred) or enter the
    Client ID + Secret by hand. Returns (id, secret)."""
    default = _latest_client_json() or ""
    print("  Point me at the Desktop OAuth client JSON you downloaded,")
    print("  or type 'manual' to enter the Client ID / Secret by hand.")
    while True:
        raw = prompt("Path to client JSON ('manual' to type creds)",
                     default=default if default else None, allow_empty=not default)
        if not raw or raw.strip().lower() == "manual":  # manual entry
            cid = prompt("OAuth Client ID", validator=VALIDATORS["nonempty"])
            sec = prompt(
                "OAuth Client Secret",
                validator=lambda v: (_looks_like_secret(v),
                    "that looks malformed (a file path?) — paste the GOCSPX-… secret value, not the JSON path"),
                secret=True)
            return cid.strip(), sec.strip()
        try:
            cid, sec = _read_oauth_client_json(raw)
            print(f"    ✓ read OAuth client {cid[:24]}… from {Path(raw).name}")
            return cid, sec
        except ValueError as e:
            print(f"    ✗ {e}")
            default = ""  # don't keep re-suggesting a bad default


def _print_google_cloud_prep(s, add_keys, first_run):
    """Cloud Console steps for the apps being added. First run also creates the ONE
    Desktop OAuth client; later runs print only the incremental APIs + scopes."""
    apis = _google_union_apis(s, add_keys)
    scopes = _google_union_scopes(s, add_keys)
    api_ids = " ".join(api_id for _, api_id in apis)
    if first_run:
        step(2, "Google Cloud Console — one-time setup (shared by every Google app)")
        print(f"""
  2a. Open <https://console.cloud.google.com/projectcreate> and create/select a
      personal project.

  2b. Enable the product APIs for the apps you picked:
        gcloud services enable {api_ids}
      …or enable each from the API Library:""")
        for name, api_id in apis:
            print(f"        • {name}: https://console.cloud.google.com/apis/library/{api_id}")
        print(f"""
  2c. Configure the OAuth consent screen ({s['consent_url']}):
        • User Type: External · add your Google account as a Test User
        • ⚠ While in "Testing", Google EXPIRES refresh tokens after ~7 days — so the
          servers would need re-auth weekly. PUBLISH the app (Testing → Production)
          to make tokens durable.
      Add these scopes ("Scopes for Google APIs") — what the local servers request:""")
        for sc in scopes:
            print(f"        {sc}")
        print(f"""
  2d. Create ONE OAuth 2.0 Client ID ({s['credentials_url']}):
        • Application type:  Desktop app  (REQUIRED — the local servers use a
          loopback OAuth redirect and rely on Google's loopback exemption; a
          "Web application" client would need every callback port pre-registered).
      Download the client-secret JSON — pass it next (or via --client-secret <path>).
""")
    else:
        step(2, "Google Cloud Console — add the new app(s) to your existing setup")
        print("  Reusing your existing Desktop OAuth client — no new client needed.")
        print(f"\n  Enable the added APIs:\n        gcloud services enable {api_ids}")
        for name, api_id in apis:
            print(f"        • {name}: https://console.cloud.google.com/apis/library/{api_id}")
        print(f"\n  Add these scopes to the SAME consent screen ({s['consent_url']}):")
        for sc in scopes:
            print(f"        {sc}")
        print()
    input("  Press Enter once the Console steps above are done… ")


# ── Google local-stdio helpers (Node-24 shim, MCP_TIMEOUT, keys, per-app auth) ──

_UNDICI_SHIM_PATH = Path.home() / ".claude" / "force-undici-fetch.cjs"
_UNDICI_SHIM_SRC = """\
// Preload shim: make require('node-fetch') return Node's native fetch (undici).
// Works around node-fetch@2 ERR_STREAM_PREMATURE_CLOSE on Node >= 24, which breaks
// gaxios/google-auth-library — the libs the local Google MCP servers rely on.
// Use via:  NODE_OPTIONS="--require <this file>"
const Module = require('module');
const origLoad = Module._load;
function undiciFetch(...args) { return globalThis.fetch(...args); }
undiciFetch.default = undiciFetch;
undiciFetch.Headers = globalThis.Headers;
undiciFetch.Request = globalThis.Request;
undiciFetch.Response = globalThis.Response;
undiciFetch.isRedirect = (code) => [301, 302, 303, 307, 308].includes(code);
class FetchError extends Error { constructor(m, type) { super(m); this.name = 'FetchError'; this.type = type; } }
class AbortError extends Error { constructor(m) { super(m); this.name = 'AbortError'; this.type = 'aborted'; } }
undiciFetch.FetchError = FetchError;
undiciFetch.AbortError = AbortError;
Module._load = function (request, parent, isMain) {
  if (request === 'node-fetch') return undiciFetch;
  return origLoad.apply(this, arguments);
};
"""


def _ensure_undici_shim():
    """Ensure the Node-24 node-fetch→undici preload shim exists; return its path
    (or None if it can't be written). The local Google servers hit gaxios/
    google-auth-library, which use node-fetch@2 and premature-close on Node >= 24."""
    try:
        _UNDICI_SHIM_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _UNDICI_SHIM_PATH.exists():
            _UNDICI_SHIM_PATH.write_text(_UNDICI_SHIM_SRC)
            print(f"  ✓ wrote Node-24 fetch shim → {_UNDICI_SHIM_PATH}")
        return _UNDICI_SHIM_PATH
    except OSError as e:
        print(f"  ⚠ couldn't write the undici shim ({e}); continuing without it.")
        return None


def _ensure_mcp_timeout(min_ms=120000):
    """Raise MCP_TIMEOUT in ~/.claude/settings.json (idempotent). The local Google
    servers import the heavy googleapis lib and take ~10s to reach initialize; on a
    multi-server session boot that can exceed Claude Code's default connect timeout,
    surfacing "failed to connect" for a perfectly healthy server."""
    settings = Path.home() / ".claude" / "settings.json"
    try:
        data = json.loads(settings.read_text()) if settings.exists() else {}
    except (OSError, ValueError):
        print(f"  ⚠ couldn't read {settings}; set MCP_TIMEOUT={min_ms} yourself.")
        return
    env = data.setdefault("env", {})
    try:
        cur = int(str(env.get("MCP_TIMEOUT", "0")))
    except ValueError:
        cur = 0
    if cur >= min_ms:
        return
    env["MCP_TIMEOUT"] = str(min_ms)
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, indent=2) + "\n")
        print(f"  ✓ set MCP_TIMEOUT={min_ms} in {settings} (slow local servers need boot time).")
    except OSError as e:
        print(f"  ⚠ couldn't write {settings} ({e}); set MCP_TIMEOUT={min_ms} yourself.")


def _write_gcp_oauth_keys(dest, client_id, client_secret):
    """Write a Desktop ('installed') OAuth client JSON the local servers read. It
    carries the client secret, so create it 0600 from the start (no world-readable
    TOCTOU window) rather than write-then-chmod."""
    payload = json.dumps({
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }, indent=2)
    # O_CREAT honours the 0600 mode for a NEW file (umask can only tighten it);
    # the chmod below covers the case where dest pre-existed with looser perms.
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)
    try:
        os.chmod(dest, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:
        print(f"  ⚠ couldn't chmod 0600 {dest} ({e}) — it holds your OAuth client secret.")


def _port_busy(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.3)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def _warn_runtime_env_footguns():
    """Warn about ambient env vars that make the Drive/Calendar servers IGNORE their
    token files at RUNTIME. `claude mcp add --env` can only ADD vars to the spawned
    server — it cannot UNSET one the server inherits from the shell that launches
    Claude Code. If these are exported, auth succeeds but the first tool call hangs."""
    hits = [k for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_DRIVE_MCP_ACCESS_TOKEN",
                        "GOOGLE_DRIVE_MCP_REFRESH_TOKEN", "GOOGLE_ACCOUNT_MODE")
            if os.environ.get(k)]
    if hits:
        print(f"\n  ⚠ {', '.join(hits)} set in your environment — the Drive/Calendar")
        print("    server would switch to service-account / external-token / alt-account")
        print("    mode and IGNORE the token file just minted. UNSET these in the shell")
        print("    or launcher that starts Claude Code, or the server will hang on first use.")


def _run_google_app_auth(app, keys_path, token_path, cwd, shim):
    """Run the app package's `auth` subcommand (opens a browser for consent) to mint
    the token file. Blocks until consent completes. Returns True on success.

    None of the three servers auto-auth on normal MCP start (Drive's first tool call
    would hang), so this explicit browser step is mandatory before registration."""
    busy = [p for p in app.get("auth_ports", []) if _port_busy(p)]
    if busy:
        print(f"  ⚠ loopback port(s) {busy} are in use — the OAuth callback needs one")
        print(f"    of {app['auth_ports']} free (e.g. a running Drive MCP squats :3000).")
        if not confirm("Continue anyway?", default=True):
            return False
    env = os.environ.copy()
    # Strip auth-mode footguns: with these set the Drive server IGNORES tokens.json,
    # and GOOGLE_ACCOUNT_MODE would file Calendar tokens under a non-'normal' key.
    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_DRIVE_MCP_ACCESS_TOKEN",
              "GOOGLE_DRIVE_MCP_REFRESH_TOKEN", "GOOGLE_ACCOUNT_MODE"):
        env.pop(k, None)
    env[app["keys_env"]] = str(keys_path)
    env[app["token_env"]] = str(token_path)
    if shim:
        prev = env.get("NODE_OPTIONS", "")
        env["NODE_OPTIONS"] = (prev + " " if prev else "") + f'--require "{shim}"'
    print(f"\n  Authorizing {app['service_name']} — a browser window opens for consent")
    print("  (if it doesn't, open the URL the command prints below):")
    # cwd = the app's state dir (which holds gcp-oauth.keys.json) so the Gmail
    # server's cwd→GMAIL_OAUTH_PATH copy is a harmless self-copy.
    result = subprocess.run(["npx", "-y", app["package"], "auth"], env=env, cwd=str(cwd))
    if result.returncode != 0:
        print(f"  ✗ auth failed for {app['service_name']} (exit {result.returncode}).")
        return False
    if not Path(token_path).exists():
        print(f"  ✗ auth exited 0 but no token file appeared at {token_path}.")
        return False
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"  ✓ {app['service_name']} authorized.")
    return True


def _google_rotate_secret(service_key, s, state):
    step(1, "Rotate the OAuth client secret")
    print("  Provide the NEW client-secret JSON for the SAME Desktop OAuth client")
    print(f"  (create a new secret in Cloud Console: {s['credentials_url']}).")
    client_id, new_secret = _prompt_oauth_client()
    handle = state["handle"]
    apps = state.get("apps", [])
    slug = slugify(handle) or "google"
    for k in apps:
        app_dir = make_state_dir(service_key, os.path.join(slug, k))
        _write_gcp_oauth_keys(app_dir / "gcp-oauth.keys.json", client_id, new_secret)
    _save_google_state(service_key, handle, client_id, new_secret, "", apps)
    print("\n  ✓ Rewrote the client-key files with the new secret. Existing tokens keep")
    print("    working (refresh_token unchanged); if a server now fails auth, re-run")
    print("    setup and re-authorize that app.")
    return 0


def prompt_select(question, options):
    """Single-select from options (list of (key, label)). Returns the chosen key.
    Enter accepts option 1."""
    print(f"  {question}")
    for i, (_, label) in enumerate(options, 1):
        print(f"    {i}. {label}")
    while True:
        raw = input("  choice [1]: ").strip() or "1"
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("    ✗ enter a number from the list")


def _prompt_new_handle(existing):
    while True:
        h = slugify(prompt("New account handle (e.g. your email prefix, like 'minaalfy8')",
                           validator=VALIDATORS["nonempty"])) or ""
        if not h:
            print("    ✗ handle can't be empty")
            continue
        if h in existing:
            print(f"    ✗ '{h}' already exists — choose it from the menu to manage it,")
            print("      or pick a different handle for the new account")
            continue
        return h


def _setup_local_oauth(service_key, s, args=None):
    """Set up Google apps as LOCAL stdio Node MCP servers (one per app). For each
    selected app: write a Desktop gcp-oauth.keys.json from the client secret, run
    the package's `auth` subcommand (browser consent → token file), then register
    the stdio server with Claude Code. CLI flags (--client-secret/--handle/--apps)
    make it non-interactive; otherwise it prompts."""
    apps = s["apps"]
    cli_secret = getattr(args, "client_secret", None)
    cli_handle = getattr(args, "handle", None)
    cli_apps = getattr(args, "apps", None)

    # STEP 0 — pick the ACCOUNT. Each Google account is INDEPENDENT: its own OAuth
    # client + its own <handle>-Gmail/Drive/Calendar local servers. Several coexist
    # (e.g. minaalfy8 and minaalfykamel); state is keyed by handle so adding a new
    # account never overwrites another.
    handles = _list_google_handles(service_key)
    step(0, "Which Google account?")
    if cli_handle:
        handle = slugify(cli_handle)
        print(f"  Using account handle '{handle}' (from --handle).")
    elif handles:
        opts = []
        for h in handles:
            st = _load_google_state(service_key, h)
            apps_str = ", ".join(sorted(st["apps"])) if st and st.get("apps") else "(none)"
            opts.append((h, f"{h}   (apps: {apps_str})"))
        opts.append(("__new__", "➕  add a DIFFERENT / new account"))
        choice = prompt_select("Manage an existing account, or add a new one:", opts)
        handle = _prompt_new_handle(handles) if choice == "__new__" else choice
    else:
        print("  No Google accounts registered yet.")
        print(f"  Docs: {s['docs_url']}")
        handle = _prompt_new_handle(handles)

    state = _load_google_state(service_key, handle)
    registered = list(state["apps"]) if state else []
    if state:
        print(f"\n  Managing '{handle}' — client {state.get('client_id','')[:20]}…,"
              f" registered apps: {', '.join(sorted(registered)) or '(none)'}")
    else:
        print(f"\n  New account '{handle}' — this gets its own OAuth client.")

    # STEP 1 — choose apps (from --apps, or interactively)
    step(1, "Choose which Google apps you want")
    if cli_apps:
        wanted = [a.strip() for a in cli_apps.split(",") if a.strip()]
        bad = [a for a in wanted if a not in apps]
        if bad:
            print(f"  ✗ unknown app(s): {bad}; valid: {list(apps)}")
            return 2
        desired = [k for k in apps if k in wanted]
        print(f"  Apps (from --apps): {', '.join(desired)}")
    else:
        options = [(k, f"{apps[k]['service_name']}  ({apps[k]['package']})") for k in apps]
        desired = prompt_multiselect(
            "Select the Google apps to enable:", options, preselected=registered)
    desired_set = set(desired)
    to_add = [k for k in apps if k in desired_set and k not in registered]
    to_remove = [k for k in apps if k in registered and k not in desired_set]
    keep = [k for k in apps if k in desired_set and k in registered]

    if not to_add and not to_remove:
        print("\n  Selection already matches what's registered — nothing to add/remove.")
        if state and not cli_secret and confirm("Rotate the OAuth client secret instead?", default=False):
            return _google_rotate_secret(service_key, s, state)
        print("  Done.  (To re-authorize an app, de-select then re-select it, or remove+add.)")
        return 0

    print(f"\n  Plan:  add={to_add or '—'}   remove={to_remove or '—'}   keep={keep or '—'}")

    # Resolve the shared Desktop OAuth client — reuse stored, or collect via
    # --client-secret / prompt.
    client_id = secret = ""
    if to_add:
        stored_ok = (state and state.get("client_id")
                     and _looks_like_secret(state.get("client_secret", "")))
        if cli_secret:
            try:
                client_id, secret = _read_oauth_client_json(cli_secret)
            except ValueError as e:
                print(f"  ✗ --client-secret: {e}")
                return 2
            print(f"  ✓ read OAuth client {client_id[:24]}… from {Path(cli_secret).name}")
            # Mirror the interactive paths' Console reminder: full prep for a new
            # account, incremental (added APIs/scopes) when adding to an existing one.
            if state:
                _print_google_cloud_prep(s, to_add, first_run=False)
            else:
                _print_google_cloud_prep(s, desired, first_run=True)
        elif stored_ok and confirm(
                f"Reuse the stored OAuth client {state['client_id'][:24]}…?", default=True):
            client_id, secret = state["client_id"], state["client_secret"]
            _print_google_cloud_prep(s, to_add, first_run=False)
        else:
            _print_google_cloud_prep(s, desired, first_run=not state)
            step(3, f"Provide the Desktop OAuth client for '{handle}'")
            print(f"  Servers will be titled '{handle}-Gmail', '{handle}-GoogleDrive', etc.")
            client_id, secret = _prompt_oauth_client()
    elif state:
        client_id, secret = state.get("client_id", ""), state.get("client_secret", "")

    # Shared prep: ensure the Node-24 fetch shim + raise MCP_TIMEOUT for slow boots,
    # and warn about ambient env vars that would make the runtime servers ignore tokens.
    shim = _ensure_undici_shim() if to_add else None
    if to_add:
        _ensure_mcp_timeout()
        _warn_runtime_env_footguns()

    slug = slugify(handle) or "google"
    titles = _google_titles(handle, s, apps)

    # STEP 4 — per app: write keys → browser auth → register stdio server.
    step(4, "Authorize + register the selected app(s)")
    added_ok = []
    for k in to_add:
        app = apps[k]
        app_dir = make_state_dir(service_key, os.path.join(slug, k))
        keys_path = app_dir / "gcp-oauth.keys.json"
        token_path = app_dir / app["token_file"]
        _write_gcp_oauth_keys(keys_path, client_id, secret)

        # Mint the token unless a valid one already exists and the user keeps it.
        if token_path.exists() and confirm(
                f"  {app['service_name']}: a token file already exists — keep it (skip browser)?",
                default=True):
            pass
        elif not _run_google_app_auth(app, keys_path, token_path, app_dir, shim):
            print(f"  ⚠ skipping {app['service_name']} — not authorized.")
            continue

        env_pairs = {app["keys_env"]: str(keys_path), app["token_env"]: str(token_path)}
        if shim:
            env_pairs["NODE_OPTIONS"] = f'--require "{shim}"'
        title = titles[k]
        subprocess.run(claude_cmd("mcp", "remove", "--scope", "user", title),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if claude_mcp_add(title, "npx", app["package"], env_pairs, step_num=4):
            added_ok.append(k)

    removed_ok = []
    for k in to_remove:
        title = titles[k]
        if confirm(f"Remove '{title}' from Claude Code?", default=True):
            subprocess.run(claude_cmd("mcp", "remove", "--scope", "user", title))
            # Delete the app's state dir too — its token file holds a live refresh
            # token; a "removed" app shouldn't leave working credentials on disk.
            shutil.rmtree(STATE_ROOT / service_key / slug / k, ignore_errors=True)
            print(f"  ✓ Removed '{title}' (and its local token).")
            removed_ok.append(k)
        else:
            print(f"  ⓘ Kept '{title}' — still registered; leaving it in tracked state.")

    # Declined removals stay registered → keep them in state (don't drop silently).
    final_apps = sorted((set(registered) - set(removed_ok)) | set(added_ok))
    final_dir = _save_google_state(service_key, handle, client_id, secret, "", final_apps)
    print(f"\n  ✓ State saved at {final_dir} (mode 600).")

    hr("═")
    if added_ok:
        print("  ✓ Done. RESTART your Claude Code session to load the new tools.")
        print("     The local Google servers take ~10s each to boot (heavy googleapis")
        print("     import) — MCP_TIMEOUT is set so they don't lose the startup race.")
    else:
        print("  ✓ Done. `claude mcp list` shows status.")
    hr("═")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Setup — api_token branch (Atlassian DC, Tempo)
# ──────────────────────────────────────────────────────────────────────────────

def _setup_api_token(service_key, s):
    step(1, f"Generate a Personal Access Token for {s['label']}")
    howto = s.get("pat_howto") or (
        'How to create a Jira DC Personal Access Token:\n'
        '    1. Sign in to your Jira instance in a browser.\n'
        '    2. Open your profile menu (top-right avatar) → "Personal Access Tokens"\n'
        '       (Data Center / Server only — Cloud uses API tokens instead).\n'
        '    3. Click "Create token", give it a name (e.g. "Claude Code MCP"),\n'
        '       set expiry as your security policy requires, and copy the token.\n'
        '    4. Paste it below when prompted.'
    )
    print(f"""
  {howto}

  Reference: {s['pat_setup_url']}

  Notes: {s['scopes_note']}
""")
    input("  Press Enter once you have your PAT ready… ")

    step(2, "Enter the connection details")
    values = {}
    for spec in s["env_vars"]:
        name = spec["name"]
        required = spec.get("required", False)
        validator_key = spec.get("validator", "nonempty")
        validator = VALIDATORS.get(validator_key)
        skip_if = spec.get("skip_if_blank")
        if skip_if and not values.get(skip_if):
            values[name] = ""
            print(f"  ↷ Skipping {name} (paired with {skip_if} which was left blank)")
            continue
        derive_from = spec.get("derive_from_env")
        if not required:
            marker = (
                f"  (optional, leave blank to derive from {derive_from})"
                if derive_from
                else "  (optional, leave blank to skip)"
            )
        else:
            marker = ""
        print(f"\n  {name}{marker}")
        print(f"    {spec['description']}")
        v = prompt(
            name,
            default=spec.get("default"),
            validator=validator,
            allow_empty=not required,
            secret=spec.get("secret", False),
        )
        # A blank optional value can inherit from a sibling env var collected
        # earlier this loop — e.g. on Atlassian Cloud / a single Data Center
        # instance, Confluence shares Jira's host (+ /wiki) and PAT. The source
        # var is listed before this one in env_vars, so it's already in `values`.
        if not v and derive_from:
            src = values.get(derive_from, "")
            if src:
                suffix = spec.get("derive_suffix", "")
                v = src.rstrip("/") + suffix if suffix else src
                shown = "********" if spec.get("secret") else v
                print(f"  ↳ left blank — derived from {derive_from}: {shown}")
        values[name] = v

    tmp_slug = "_pending"
    state_dir = make_state_dir(service_key, tmp_slug)
    env_path = state_dir / "env"
    write_env_file(env_path, values)
    print(f"\n  ✓ env file staged at {env_path} (mode 600)")

    step(5, "Pick a title for this MCP server in Claude Code")
    print("  Note: claude mcp names accept letters, numbers, hyphens, and underscores only.")
    source_env = s.get("title_source_env")
    source_value = values.get(source_env, "") if source_env else ""
    title_kind = s.get("title_source_kind")
    if source_value and title_kind == "ado_org_from_url":
        host_slug = ado_org_from_url(source_value) or host_from_url(source_value)
    elif source_value:
        host_slug = host_from_url(source_value)
    else:
        host_slug = "host"
    print(f"  Default is <slug>-{s['service_name']} (e.g. {host_slug}-{s['service_name']}).")
    default_title = f"{host_slug}-{s['service_name']}"
    title = prompt("Title", default=default_title, validator=validate_mcp_name)

    final_slug = slugify(title)
    final_dir = STATE_ROOT / service_key / final_slug
    replacing = final_dir.exists() and final_dir != state_dir
    if replacing:
        print(f"  ⓘ State dir already exists for slug '{final_slug}': {final_dir}")
        if not confirm("Replace existing state with the freshly entered token?", default=True):
            print("     Pick a different title or remove the existing state dir first.")
            return 1
        shutil.rmtree(final_dir)
        print(f"  ✓ removed old state dir")
    if final_dir != state_dir:
        state_dir.rename(final_dir)
        env_path = final_dir / "env"
        print(f"  ✓ state moved to {final_dir}")

    if replacing:
        subprocess.run(
            claude_cmd("mcp", "remove", "--scope", "user", title),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    package = s.get("package") or s.get("npx_package")
    ok = claude_mcp_add(
        title=title,
        launcher=s["launcher"],
        package=package,
        env_pairs=values,
    )
    if not ok:
        return 1

    if not _finalize_with_handshake(
        title=title, final_dir=final_dir,
        launcher=s["launcher"], package=package, env_pairs=values,
        step_num=6,
    ):
        return 1

    hr("═")
    print(f"  ✓ Done. '{title}' is registered and verified.")
    print(f"    State:  {final_dir}")
    print(f"    Rotate the PAT later by re-running this command with the same title.")
    print(f"    Run `claude mcp list` to confirm and restart your session.")
    hr("═")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Setup — cookie_paste branch (LinkedIn)
# ──────────────────────────────────────────────────────────────────────────────

def _setup_cookie_paste(service_key, s):
    local_dir = (REPO_ROOT / s["local_pkg_dir"]).resolve()
    if not local_dir.exists():
        print(f"  ✗ Local package directory not found: {local_dir}")
        return 1

    step(1, f"Grab your LinkedIn session cookies for {s['label']}")
    print("""
  Open LinkedIn in your browser and sign in normally (with 2FA if you use it).
  Then extract two cookies via DevTools:

    Chrome / Edge:
      1. Press F12 (DevTools)
      2. Application tab → Storage → Cookies → https://www.linkedin.com
      3. Copy the 'Value' column for these two rows:
           • li_at          (long random string, ~120+ chars)
           • JSESSIONID     (looks like 'ajax:1234567890123456')
      4. Paste each value at the prompts below.

    Firefox:
      1. Press F12 → Storage tab → Cookies → https://www.linkedin.com
      2. Copy 'Value' for li_at and JSESSIONID.

  Notes:
   - Paste the VALUE only, no surrounding quotes.
   - Cookies last ~90 days unless you log out / change password.
   - If you ever rotate, re-run this script with the same title to update.
   - Treat li_at like a password — anyone with it can post as you.

  ⚠ Account-safety reminder: this MCP throttles itself (daily caps + working
     hours), but unofficial API use against LinkedIn can still get an account
     restricted. With your eAT job-hunt active, don't run engagement actions
     in bulk — the read tools (jobs, profiles, feed, inbox) are much safer.
""")
    input("  Press Enter once you have both cookies ready… ")

    step(2, "Enter the cookies + optional config")
    values = {}
    for spec in s["env_vars"]:
        name = spec["name"]
        required = spec.get("required", False)
        validator = VALIDATORS.get(spec.get("validator", "nonempty"))
        marker = "" if required else "  (optional, leave blank for default)"
        print(f"\n  {name}{marker}")
        print(f"    {spec['description']}")
        v = prompt(
            name,
            validator=validator,
            allow_empty=not required,
            secret=spec.get("secret", False),
        )
        values[name] = (v or "").strip().strip('"').strip("'")

    # Stage state dir with a pending slug
    tmp_slug = "_pending"
    state_dir = make_state_dir(service_key, tmp_slug)
    env_path = state_dir / "env"
    write_env_file(env_path, values)
    print(f"\n  ✓ env file staged at {env_path} (mode 600)")

    step(3, "Connection test — call get_user_profile() with the pasted cookies")
    ping_env = os.environ.copy()
    ping_env.update({k: v for k, v in values.items() if v})
    ping_env["LINKEDIN_STATE_DIR"] = str(state_dir)
    ping_cmd = ["uv", "run", "--directory", str(local_dir), "python", "-m", "linkedin_mcp.ping"]
    try:
        result = subprocess.run(
            ping_cmd, env=ping_env, capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print("  ✗ Ping timed out after 120s. Check your network + cookie values.")
        return 1
    except FileNotFoundError:
        print("  ✗ `uv` not found on PATH. Install uv (https://docs.astral.sh/uv/) and re-run.")
        return 1
    if result.returncode != 0:
        print(f"  ✗ Ping failed (exit {result.returncode}).")
        if result.stderr.strip():
            print("    stderr:")
            for line in result.stderr.strip().splitlines():
                print(f"      {line}")
        if "AUTH-FAILED" in (result.stderr or ""):
            print("    → li_at is invalid / expired. Re-copy from DevTools and retry.")
        return 1
    try:
        identity = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        print(f"  ⚠ ping returned non-JSON: {result.stdout!r}")
        identity = {}
    name = " ".join(filter(None, [identity.get("first_name"), identity.get("last_name")])) or "(unknown)"
    print(f"  ✓ Connected as: {name}")
    if identity.get("headline"):
        print(f"    Headline: {identity['headline']}")

    step(4, "Pick a title for this MCP server in Claude Code")
    print("  Note: claude mcp names accept letters, numbers, hyphens, and underscores only.")
    default_label = values.get("LINKEDIN_ACCOUNT_LABEL") or slugify(name) or "linkedin"
    default_title = f"{default_label}-{s['service_name']}"
    title = prompt("Title", default=default_title, validator=validate_mcp_name)

    final_slug = slugify(title)
    final_dir = STATE_ROOT / service_key / final_slug
    replacing = final_dir.exists() and final_dir != state_dir
    if replacing:
        print(f"  ⓘ State dir already exists for slug '{final_slug}': {final_dir}")
        if not confirm("Replace existing state with the freshly entered cookie?", default=True):
            print("     Pick a different title or remove the existing state dir first.")
            return 1
        shutil.rmtree(final_dir)
        print(f"  ✓ removed old state dir")
    if final_dir != state_dir:
        state_dir.rename(final_dir)
        env_path = final_dir / "env"
        print(f"  ✓ state moved to {final_dir}")

    # The server needs LINKEDIN_STATE_DIR pointing at its final dir so the
    # throttle counter file + playwright profile dir resolve correctly.
    if not values.get("LINKEDIN_ACCOUNT_LABEL"):
        values["LINKEDIN_ACCOUNT_LABEL"] = default_label
    env_pairs = dict(values)
    env_pairs["LINKEDIN_STATE_DIR"] = str(final_dir)

    if replacing:
        subprocess.run(
            claude_cmd("mcp", "remove", "--scope", "user", title),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    ok = claude_mcp_add(
        title=title,
        launcher=s["launcher"],
        package=s["local_pkg_dir"],
        env_pairs=env_pairs,
        local_dir=local_dir,
        console_script=s.get("local_pkg_console_script"),
    )
    if not ok:
        return 1

    if not _finalize_with_handshake(
        title=title, final_dir=final_dir,
        launcher=s["launcher"], package=s["local_pkg_dir"], env_pairs=env_pairs,
        local_dir=local_dir, console_script=s.get("local_pkg_console_script"),
        step_num=6,
    ):
        return 1

    hr("═")
    print(f"  ✓ Done. '{title}' is registered and verified.")
    print(f"    State:  {final_dir}")
    print(f"    Restart your Claude Code session to load tools.")
    print(f"    Rotate cookies later by re-running this command with the same title.")
    hr("═")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Setup — entra_login branch (Azure DevOps via Microsoft @azure-devops/mcp)
# ──────────────────────────────────────────────────────────────────────────────

_ADO_ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TENANT_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")
_ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


def _ado_probe_creds(org, auth_header_value, timeout=15):
    """Probe https://dev.azure.com/<org>/_apis/projects with the given Authorization
    header value (full header value — caller decides 'Basic <b64>' vs 'Bearer <token>').

    Returns (ok: bool, message: str). Distinguishes:
      • 200 + JSON  → ok, includes project count
      • 401         → invalid / expired / wrong-org PAT
      • 203 / HTML  → unauthenticated redirect to sign-in
      • 404         → org doesn't exist (typo)
      • network err → toolkit can't reach ADO from this machine
    """
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    url = f"https://dev.azure.com/{org}/_apis/projects?api-version=7.1-preview.4&$top=1"
    req = Request(url, headers={"Authorization": auth_header_value, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            ct = (resp.headers.get("Content-Type") or "").lower()
            body = resp.read()
            if resp.status == 200 and "application/json" in ct:
                try:
                    data = json.loads(body)
                    count = data.get("count", 0)
                    return True, f"PAT works — {count} project(s) visible in '{org}'"
                except Exception:
                    return False, "200 OK but body wasn't JSON — unexpected ADO response"
            if "text/html" in ct or resp.status in (203, 302, 303):
                return False, (
                    f"ADO returned HTTP {resp.status} with a sign-in redirect — "
                    "credentials were rejected (often: PAT belongs to a different org, "
                    "or the org name is wrong)"
                )
            return False, f"Unexpected HTTP {resp.status} (content-type {ct})"
    except HTTPError as e:
        if e.code == 401:
            return False, "401 Unauthorized — PAT is invalid, expired, or has no access to this org"
        if e.code == 403:
            return False, "403 Forbidden — PAT scope is too narrow (need at least 'Project and team: Read')"
        if e.code == 404:
            return False, f"404 Not Found — org '{org}' doesn't exist or you have no access"
        return False, f"HTTP {e.code} — {e.reason}"
    except URLError as e:
        return False, f"Network error reaching {url}: {e.reason}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def _ado_collect_and_encode_pat(org, cached_email_default):
    """Prompt for email + raw PAT separately, base64('email:token') for the user.
    Returns (pat_b64, email)."""
    print()
    print("  Where to create the PAT:")
    print(f"    https://dev.azure.com/{org}/_usersSettings/tokens")
    print()
    print("  Scopes — for setup-time verification you need AT LEAST:")
    print("    • Project and team: Read")
    print("  Add the others you'll actually use (commonly):")
    print("    • Code: Read & write    • Work items: Read & write")
    print("    • Build: Read           • Wiki: Read & write")
    print("    • Identity: Read        • Test management: Read")
    print()
    print("  Expiry — pick a date you're comfortable rotating (90 days is a reasonable default).")
    print("  This script will base64-encode 'email:PAT' for you — paste them separately below.")
    print()
    email = prompt(
        "Microsoft account email (the one that owns the PAT)",
        default=cached_email_default,
        validator=lambda v: (EMAIL_RE.match(v) is not None, "doesn't look like an email"),
    )
    raw_pat = prompt(
        "Raw PAT (paste the value as shown right after creation — no encoding)",
        validator=lambda v: (len(v.strip()) >= 30, "ADO PATs are typically 52+ chars; that looks too short"),
        secret=True,
    )
    raw_pat = raw_pat.strip()
    pat_b64 = base64.b64encode(f"{email}:{raw_pat}".encode("utf-8")).decode("ascii")
    return pat_b64, email


def _setup_entra_login(service_key, s):
    step(1, f"{s['label']} — find your organisation name")
    print("""
  Azure DevOps URLs look like:  https://dev.azure.com/<organisation>
  Your <organisation> is the trailing path segment, used as a positional CLI
  arg to the MCP server. The vendor has no 'list organisations' tool — the
  org is fixed at setup time.

  If you don't remember which orgs your account belongs to, visit:
    https://aex.dev.azure.com/
  (signed-in landing page that lists every Azure DevOps org tied to your
   Microsoft account, with direct dev.azure.com links).
""")
    org = prompt(
        "Organisation name (the trailing segment after dev.azure.com/)",
        validator=lambda v: (
            bool(_ADO_ORG_RE.match(v)),
            "expected an ADO org slug (letters/digits/._-, ≤64 chars)",
        ),
    )

    step(2, "Pick the auth method")
    print("""
    pat          PAT in PERSONAL_ACCESS_TOKEN env — VALIDATED at setup    [recommended]
    azcli        local `az login` session — VALIDATED at setup
    envvar       raw bearer token in ADO_MCP_AUTH_TOKEN env — VALIDATED at setup
    interactive  Entra browser flow on the FIRST TOOL CALL (not at setup)
    env          Azure SDK env-credential chain — NOT validated (advanced)

  PAT is the default because it's self-contained (no browser, no az CLI), the
  toolkit can verify it during setup, and rotation is a re-run of this script.
""")
    auth_method = prompt(
        "Auth method",
        default="pat",
        validator=lambda v: (
            v in ("interactive", "azcli", "pat", "envvar", "env"),
            "expected one of: pat, azcli, envvar, interactive, env",
        ),
    )

    step(3, "Tenant + domain scoping (both optional)")
    tenant = prompt(
        "Azure tenant ID — UUID, used with interactive/azcli for multi-tenant accounts (blank = common)",
        allow_empty=True,
        validator=lambda v: (
            not v or bool(_TENANT_RE.match(v)),
            "expected a tenant UUID or leave blank",
        ),
    )
    print()
    print("  Domains restrict which tool groups are loaded (smaller = less noise in tool picker).")
    print("  'all' (default) loads everything; or space-separate e.g. 'repositories work-items'.")
    domains_input = prompt("Domains to enable", default="all")

    env_pairs = {}

    # ── Auth-method-specific collection + LIVE VALIDATION ──────────────────
    if auth_method == "pat":
        step(4, "Personal Access Token — collect + validate against Azure DevOps")
        pat_b64, email = _ado_collect_and_encode_pat(org, load_cached_email())
        print()
        print(f"  Probing https://dev.azure.com/{org}/_apis/projects to verify the PAT…")
        ok, msg = _ado_probe_creds(org, f"Basic {pat_b64}")
        if not ok:
            print(f"  ✗ {msg}")
            print()
            print("  Common causes & fixes:")
            print("   • Wrong org name → re-run, double-check at https://aex.dev.azure.com/")
            print("   • PAT belongs to a different Microsoft account → sign in as that account when creating it")
            print("   • PAT expired or revoked → create a new one (link printed above)")
            print("   • Missing scope → tick at least 'Project and team: Read' on the PAT")
            print("   • Corporate AAD policy blocks PAT use → switch to 'azcli' auth method")
            return 1
        print(f"  ✓ {msg}")
        save_cached_email(email)
        env_pairs["PERSONAL_ACCESS_TOKEN"] = pat_b64

    elif auth_method == "envvar":
        step(4, "Bearer token — collect + validate against Azure DevOps")
        print("""
  ADO_MCP_AUTH_TOKEN expects a RAW bearer token for the Azure DevOps resource
  (audience 499b84ac-1321-427f-aa17-267ca6975798). Get one from your IdP /
  service principal — this is the advanced path; most users want 'pat' or 'azcli'.
""")
        token = prompt(
            "ADO_MCP_AUTH_TOKEN (raw bearer)",
            validator=VALIDATORS["nonempty"],
            secret=True,
        )
        print()
        print(f"  Probing https://dev.azure.com/{org}/_apis/projects with this bearer…")
        ok, msg = _ado_probe_creds(org, f"Bearer {token}")
        if not ok:
            print(f"  ✗ {msg}")
            print("    Verify the token is freshly issued for the ADO audience and not expired.")
            return 1
        print(f"  ✓ {msg}")
        env_pairs["ADO_MCP_AUTH_TOKEN"] = token

    elif auth_method == "azcli":
        step(4, "Azure CLI — check installation, login state, fetch token, validate")
        if not shutil.which("az"):
            print("  ✗ `az` CLI not found on PATH.")
            print("    Install: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli")
            print("    Then re-run this setup.")
            return 1
        account = subprocess.run(["az", "account", "show", "-o", "json"], capture_output=True, text=True)
        if account.returncode != 0:
            print("  ⓘ You're not signed in to `az` yet.")
            login_cmd = ["az", "login"]
            if tenant:
                login_cmd += ["--tenant", tenant]
            print(f"    Running: {' '.join(login_cmd)}")
            login = subprocess.run(login_cmd)
            if login.returncode != 0:
                print("  ✗ `az login` failed. Sign in manually and re-run.")
                return 1
            account = subprocess.run(["az", "account", "show", "-o", "json"], capture_output=True, text=True)
            if account.returncode != 0:
                print("  ✗ Still not signed in after `az login`. Aborting.")
                return 1
        try:
            account_info = json.loads(account.stdout or "{}")
            signed_in_as = account_info.get("user", {}).get("name", "(unknown)")
            current_tenant = account_info.get("tenantId", "(unknown)")
            print(f"  ✓ Signed in as {signed_in_as} (tenant {current_tenant})")
        except Exception:
            pass
        token_cmd = ["az", "account", "get-access-token", "--resource", _ADO_SCOPE.split("/")[0], "-o", "json"]
        if tenant:
            token_cmd += ["--tenant", tenant]
        tok = subprocess.run(token_cmd, capture_output=True, text=True)
        if tok.returncode != 0:
            print(f"  ✗ Couldn't fetch an ADO-scoped token via az: {tok.stderr.strip()}")
            return 1
        try:
            bearer = json.loads(tok.stdout)["accessToken"]
        except Exception as e:
            print(f"  ✗ Couldn't parse `az account get-access-token` output: {e}")
            return 1
        print(f"  Probing https://dev.azure.com/{org}/_apis/projects with the az-issued bearer…")
        ok, msg = _ado_probe_creds(org, f"Bearer {bearer}")
        if not ok:
            print(f"  ✗ {msg}")
            print("    Your az session works but doesn't authorise this ADO org.")
            print("    Confirm the org name + that your sign-in account has access at https://aex.dev.azure.com/")
            return 1
        print(f"  ✓ {msg}")
        # No env vars to set — vendor will re-fetch the token via DefaultAzureCredential at runtime.

    elif auth_method == "interactive":
        step(4, "Interactive Entra — heads-up about deferred auth")
        print("""
  ⚠ The 'interactive' method does NOT authenticate during setup. The Entra
     browser flow opens the FIRST TIME a tool is called, with the MCP server
     running inside Claude Code's stdio sandbox. In practice:
        • If your desktop has a default browser and isn't sandboxed (snap /
          flatpak), the tab opens and you sign in once. Subsequent tool calls
          re-use the MSAL token cache.
        • If you're inside a snap-confined editor (e.g. snap VS Code) the
          browser-open silently fails and the tool call hangs.

  Strongly recommended: cancel and choose 'pat' or 'azcli' (both validated now).
""")
        if not confirm("Stick with interactive anyway?", default=False):
            print("  Aborted. Re-run and pick 'pat' or 'azcli'.")
            return 1

    elif auth_method == "env":
        step(4, "Azure SDK env-credential chain — no validation possible")
        print("""
  The vendor delegates to DefaultAzureCredential, which walks the env-credential
  chain (AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET, plus managed
  identity, workload identity, etc.). The toolkit can't validate this combo
  without inspecting your env, so we proceed without a probe — be ready to debug
  via the vendor's stderr if the first tool call fails.
""")
        if not confirm("Proceed without validation?", default=False):
            print("  Aborted.")
            return 1

    # ── Stage state, pick title, register ──────────────────────────────────
    tmp_slug = "_pending"
    state_dir = make_state_dir(service_key, tmp_slug)
    env_path = state_dir / "env"
    write_env_file(env_path, env_pairs)
    if env_pairs:
        print(f"\n  ✓ env file staged at {env_path} (mode 600)")

    step(5, "Pick a title for this MCP server in Claude Code")
    print("  Note: claude mcp names accept letters, numbers, hyphens, underscores only.")
    org_slug = slugify(org)
    default_title = f"{org_slug}-{s['service_name']}"
    title = prompt("Title", default=default_title, validator=validate_mcp_name)

    final_slug = slugify(title)
    final_dir = STATE_ROOT / service_key / final_slug
    replacing = final_dir.exists() and final_dir != state_dir
    if replacing:
        print(f"  ⓘ State dir already exists for slug '{final_slug}': {final_dir}")
        if not confirm("Replace existing state?", default=True):
            print("     Pick a different title or remove the existing state dir first.")
            return 1
        shutil.rmtree(final_dir)
        print(f"  ✓ removed old state dir")
    if final_dir != state_dir:
        state_dir.rename(final_dir)
        env_path = final_dir / "env"
        print(f"  ✓ state moved to {final_dir}")

    extra_args = [org]
    if domains_input and domains_input != "all":
        extra_args += ["-d"] + domains_input.split()
    extra_args += ["--authentication", auth_method]
    if tenant:
        extra_args += ["--tenant", tenant]

    if replacing:
        subprocess.run(
            claude_cmd("mcp", "remove", "--scope", "user", title),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    ok = claude_mcp_add(
        title=title,
        launcher=s["launcher"],
        package=s["npx_package"],
        env_pairs=env_pairs,
        extra_args=extra_args,
        step_num=6,
    )
    if not ok:
        return 1

    if not _finalize_with_handshake(
        title=title, final_dir=final_dir,
        launcher=s["launcher"], package=s["npx_package"], env_pairs=env_pairs,
        extra_args=extra_args, step_num=7,
    ):
        return 1

    hr("═")
    print(f"  ✓ Done. '{title}' is registered, credentials are verified, and the server answers MCP handshake.")
    print(f"    State:  {final_dir}")
    if auth_method == "interactive":
        print("    ⚠ The vendor will open an Entra browser tab on the first tool call.")
    elif auth_method == "azcli":
        print("    ⓘ Vendor re-fetches a token via your `az` session on every start.")
        print("       If `az` later logs out, the MCP server will fail until you `az login` again.")
    elif auth_method == "pat":
        print("    ⓘ Rotate the PAT later by re-running this command with the same title.")
    print(f"    Run `claude mcp list` to confirm, then restart your session.")
    hr("═")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Setup — remote_http branch (GitHub via the official remote MCP server)
# ──────────────────────────────────────────────────────────────────────────────

def _github_probe_token(pat, timeout=15):
    """Probe https://api.github.com/user with the PAT to confirm it's valid
    before we register. Returns (ok, login, message). Mirrors the ADO probe:
    validate creds at setup so a green 'done' means the next session works."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    req = Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "mcp-toolkit-setup",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            login = data.get("login")
            # Classic PATs report granted scopes here; fine-grained PATs don't.
            scopes = resp.headers.get("X-OAuth-Scopes")
            scope_note = ""
            if scopes is not None:
                scope_note = f"; classic-PAT scopes: [{scopes.strip() or 'none'}]"
            elif login:
                scope_note = "; fine-grained PAT (per-resource permissions)"
            return True, login, f"token valid — authenticated as '{login}'{scope_note}"
    except HTTPError as e:
        if e.code == 401:
            return False, None, "401 Unauthorized — token is invalid, expired, or revoked"
        if e.code == 403:
            return False, None, "403 Forbidden — token blocked or rate-limited (check SSO authorisation if org-protected)"
        return False, None, f"HTTP {e.code} — {e.reason}"
    except URLError as e:
        return False, None, f"Network error reaching api.github.com: {e.reason}"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"


def _parse_rpc_response(raw, content_type):
    """Pull the first JSON-RPC object out of an MCP HTTP response, which may be
    a plain JSON body or an SSE (text/event-stream) frame of `data:` lines."""
    if not raw:
        return None
    ct = (content_type or "").lower()
    if "text/event-stream" in ct:
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            try:
                return json.loads(payload)
            except Exception:
                continue
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _http_rpc_post(url, headers, payload, timeout=30):
    """POST one JSON-RPC message to a streamable-HTTP MCP endpoint.
    Returns (message_or_None, session_id, status, raw_body)."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    body = json.dumps(payload).encode("utf-8")
    h = dict(headers)
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json, text/event-stream"
    req = Request(url, data=body, headers=h, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            ct = resp.headers.get("Content-Type")
            session_id = resp.headers.get("Mcp-Session-Id")
            return _parse_rpc_response(raw, ct), session_id, resp.status, raw
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = e.reason
        return None, None, e.code, detail
    except URLError as e:
        return None, None, None, f"network error: {e.reason}"
    except Exception as e:
        return None, None, None, f"unexpected error: {e}"


def _http_mcp_handshake(url, headers, timeout=30):
    """Drive initialize + (initialized) + tools/list against a remote HTTP MCP
    server. Returns (ok, message, tool_count). Same contract as the stdio
    handshake so the rollback flow can treat them uniformly."""
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "setup-mcp.py", "version": "0"},
        },
    }
    msg, session_id, status, raw = _http_rpc_post(url, headers, init, timeout)
    if msg is None:
        return False, f"no JSON-RPC response to initialize (HTTP {status}): {str(raw)[:200]}", 0
    if "error" in msg:
        return False, f"initialize errored: {msg['error']}", 0
    result = msg.get("result", {}) or {}
    sinfo = result.get("serverInfo", {}) or {}
    server_info = f"{sinfo.get('name', '?')} v{sinfo.get('version', '?')}"

    h2 = dict(headers)
    if session_id:
        h2["Mcp-Session-Id"] = session_id
    # initialized is a notification — no response expected (202 + empty body).
    _http_rpc_post(url, h2, {"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)

    tmsg, _, _, _ = _http_rpc_post(url, h2, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout)
    tools_count = 0
    if tmsg and "result" in tmsg and isinstance(tmsg["result"].get("tools"), list):
        tools_count = len(tmsg["result"]["tools"])
    return True, f"{server_info} responded — {tools_count} tools exposed", tools_count


def _claude_mcp_add_http(title, url, headers, step_num=5):
    cmd = claude_cmd("mcp", "add", "--transport", "http", "--scope", "user", title, url)
    for k, v in headers.items():
        cmd += ["--header", f"{k}: {v}"]
    step(step_num, f"Registering with Claude Code as '{title}' (HTTP transport)")
    print("  command:")
    redacted = []
    skip_next = False
    for tok in cmd:
        if skip_next:
            # header value looks like "Authorization: Bearer <secret>"
            if ":" in tok:
                name, _ = tok.split(":", 1)
                redacted.append(f"{name}: ***")
            else:
                redacted.append(tok)
            skip_next = False
            continue
        if tok == "--header":
            skip_next = True
        redacted.append(tok)
    print("    " + " ".join(_shellquote(c) for c in redacted))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ✗ claude mcp add failed (exit {result.returncode}).")
        return False
    print(f"  ✓ Registered. Restart your Claude Code session to load tools.")
    return True


def _finalize_with_http_handshake(title, final_dir, url, headers, step_num=None):
    """Post-registration MCP handshake over HTTP. On failure, roll back the
    `claude mcp add` so Claude Code doesn't carry a broken handle."""
    if step_num is not None:
        step(step_num, "Smoke test — MCP handshake against the registered HTTP server")
    print("  Sending initialize + tools/list to the remote endpoint…")
    ok, msg, _ = _http_mcp_handshake(url, headers)
    if ok:
        print(f"  ✓ {msg}")
        return True
    print(f"  ✗ Handshake failed: {msg}")
    print()
    print("  Rolling back the registration so the session won't load a broken server…")
    rm = subprocess.run(
        claude_cmd("mcp", "remove", "--scope", "user", title),
        capture_output=True, text=True,
    )
    if rm.returncode == 0:
        print(f"  ✓ unregistered '{title}'.")
    else:
        print(f"  ⚠ rollback failed (exit {rm.returncode}): {rm.stderr.strip()}")
        print(f"     Run manually: claude mcp remove --scope user {title}")
    print(f"  ⓘ State dir preserved at {final_dir} for debugging.")
    return False


def _setup_remote_http(service_key, s):
    url = s["remote_url"]

    step(1, f"Create a GitHub Personal Access Token for {s['label']}")
    print(f"""
  This server is hosted by GitHub at:
    {url}
  No local install and nothing to vendor — auth is a GitHub PAT sent as an
  'Authorization: Bearer <PAT>' header on the HTTP transport.

  Create the token here:
    {s['pat_setup_url']}

  Notes: {s['scopes_note']}
""")
    input("  Press Enter once you have your PAT ready… ")

    step(2, "Enter the token")
    spec = s["env_vars"][0]
    print(f"  {spec['name']}")
    print(f"    {spec['description']}")
    pat = prompt(
        spec["name"],
        validator=VALIDATORS.get(spec.get("validator", "nonempty")),
        secret=spec.get("secret", False),
    ).strip()

    step(3, "Validate the token against the GitHub API")
    print("  Probing https://api.github.com/user to verify the PAT…")
    ok, login, msg = _github_probe_token(pat)
    if not ok:
        print(f"  ✗ {msg}")
        print()
        print("  Common causes & fixes:")
        print("   • Token mistyped / truncated → re-copy the raw value from github.com")
        print("   • Token expired or revoked → create a new one (link printed above)")
        print("   • Org enforces SSO → click 'Configure SSO' / 'Authorize' on the token page")
        return 1
    print(f"  ✓ {msg}")

    # Stage state (PAT stored for rotation/inspection; registration uses --header).
    tmp_slug = "_pending"
    state_dir = make_state_dir(service_key, tmp_slug)
    env_path = state_dir / "env"
    write_env_file(env_path, {spec["name"]: pat})
    print(f"\n  ✓ token staged at {env_path} (mode 600)")

    step(4, "Pick a title for this MCP server in Claude Code")
    print("  Note: claude mcp names accept letters, numbers, hyphens, underscores only.")
    login_slug = slugify(login or "") or "github"
    default_title = f"{login_slug}-{s['service_name']}"
    print(f"  Default is <login>-{s['service_name']} (e.g. {default_title}).")
    title = prompt("Title", default=default_title, validator=validate_mcp_name)

    final_slug = slugify(title)
    final_dir = STATE_ROOT / service_key / final_slug
    replacing = final_dir.exists() and final_dir != state_dir
    if replacing:
        print(f"  ⓘ State dir already exists for slug '{final_slug}': {final_dir}")
        if not confirm("Replace existing state with the freshly entered token?", default=True):
            print("     Pick a different title or remove the existing state dir first.")
            return 1
        shutil.rmtree(final_dir)
        print(f"  ✓ removed old state dir")
    if final_dir != state_dir:
        state_dir.rename(final_dir)
        env_path = final_dir / "env"
        print(f"  ✓ state moved to {final_dir}")

    if replacing:
        subprocess.run(
            claude_cmd("mcp", "remove", "--scope", "user", title),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    headers = {"Authorization": f"Bearer {pat}"}
    if not _claude_mcp_add_http(title, url, headers, step_num=5):
        return 1

    if not _finalize_with_http_handshake(title, final_dir, url, headers, step_num=6):
        return 1

    hr("═")
    print(f"  ✓ Done. '{title}' is registered, the PAT is verified, and the server answers MCP handshake.")
    print(f"    State:  {final_dir}")
    print(f"    Endpoint: {url}")
    print(f"    Rotate the PAT later by re-running this command with the same title.")
    print(f"    Run `claude mcp list` to confirm, then restart your session.")
    hr("═")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Top-level service setup
# ──────────────────────────────────────────────────────────────────────────────

def cmd_setup(service_key, args=None):
    if service_key not in SERVICES:
        print(f"unknown service: {service_key}; expected one of {list(SERVICES)}")
        return 2
    s = SERVICES[service_key]

    print()
    hr("═")
    print(f"  {s['label']} MCP setup")
    hr("═")

    # Fail here rather than mid-flow, so a missing CLI never costs you a
    # pasted credential (GH-13).
    if not require_claude_bin():
        return 1

    if s["auth_kind"] == "local_oauth":
        return _setup_local_oauth(service_key, s, args)
    if s["auth_kind"] == "api_token":
        return _setup_api_token(service_key, s)
    if s["auth_kind"] == "cookie_paste":
        return _setup_cookie_paste(service_key, s)
    if s["auth_kind"] == "entra_login":
        return _setup_entra_login(service_key, s)
    if s["auth_kind"] == "remote_http":
        return _setup_remote_http(service_key, s)
    print(f"unknown auth_kind '{s['auth_kind']}' for service '{service_key}'")
    return 2


# ──────────────────────────────────────────────────────────────────────────────
# Doctor
# ──────────────────────────────────────────────────────────────────────────────

def cmd_doctor():
    print("\n=== Registered MCP servers (per `claude mcp list`) ===")
    if require_claude_bin():
        print(f"  CLI: {_CLAUDE_BIN}")
        subprocess.run(claude_cmd("mcp", "list"))
    print("\n=== Local state under ./state/ ===")
    if not STATE_ROOT.exists():
        print("  (none — run a setup-* command first)")
        return 0
    for service_dir in sorted(STATE_ROOT.iterdir()):
        if not service_dir.is_dir():
            continue
        print(f"\n  {service_dir.name}/")
        for slug_dir in sorted(service_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            files = sorted(f.name for f in slug_dir.iterdir() if f.is_file())
            print(f"    {slug_dir.name}/  {files}")
            # Google local state nests one level deeper: <slug>/<app>/{keys, token}.
            for sub in sorted(d for d in slug_dir.iterdir() if d.is_dir()):
                sub_files = sorted(f.name for f in sub.iterdir() if f.is_file())
                print(f"      {sub.name}/  {sub_files}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        prog="setup-mcp.py",
        description="Set up MCP servers (Google, Atlassian, Tempo, LinkedIn, Azure DevOps, GitHub, GitLab) for Claude Code.",
    )
    sub = ap.add_subparsers(dest="service", required=True)
    for key, cfg in SERVICES.items():
        p = sub.add_parser(key, help=f"Set up the {cfg['label']} MCP server.")
        if cfg.get("auth_kind") == "local_oauth":
            p.add_argument("--client-secret", metavar="PATH",
                           help="Path to the downloaded Desktop OAuth client-secret JSON "
                                "(skips the 'point me at the JSON' prompt; runs non-interactively).")
            p.add_argument("--handle", metavar="NAME",
                           help="Account handle to set up (e.g. minaalfykamel) without the menu.")
            p.add_argument("--apps", metavar="LIST",
                           help="Comma-separated apps to enable (e.g. gmail,drive,calendar).")
    sub.add_parser("doctor", help="List registered servers and their local state.")
    # alias: `linkedin` works as a positional even though the dict key matches.
    args = ap.parse_args()
    if args.service == "doctor":
        return cmd_doctor()
    return cmd_setup(args.service, args)


if __name__ == "__main__":
    sys.exit(main())

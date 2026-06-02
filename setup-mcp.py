#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "google-auth>=2.20",
#   "google-auth-oauthlib>=1.2",
#   "google-api-python-client>=2.100",
# ]
# ///
"""
setup-mcp.py — interactive setup for MCP servers.

Usage:
    ./setup-mcp.py google-gmail
    ./setup-mcp.py google-calendar
    ./setup-mcp.py google-drive
    ./setup-mcp.py atlassian-sooperset
    ./setup-mcp.py tempo-filler
    ./setup-mcp.py azure-devops              # Microsoft official, Entra/azcli/PAT
    ./setup-mcp.py azure-devops-tiberriver   # Community PAT fallback
    ./setup-mcp.py github                    # GitHub official remote server (HTTP + PAT)
    ./setup-mcp.py linkedin
    ./setup-mcp.py doctor                    # list registered MCPs + their state

Or use the wrapper scripts under ./bin/.

Four auth flavours are supported:

  • oauth_browser  (Google services)
    1. Prints the Google Cloud Console steps you need to do manually.
    2. Waits for you to drop the credentials JSON into the path you'll point it at.
    3. Delegates OAuth to the vendor's own `auth` subcommand.
    4. Best-effort connection test against the service's identity endpoint.
    5. Prompts for a server title (defaults to `<email-handle>-<ServiceName>`).
    6. Registers via `claude mcp add --scope user -- npx -y <pkg>`.

  • api_token  (Atlassian DC, Tempo Server, Azure DevOps via Tiberriver256)
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


def load_cached_email():
    if not EMAIL_CACHE.exists():
        return None
    value = EMAIL_CACHE.read_text().strip()
    return value if EMAIL_RE.match(value) else None


def save_cached_email(email):
    EMAIL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    EMAIL_CACHE.write_text(email + "\n")


def email_handle(email):
    handle = email.split("@", 1)[0]
    handle = re.sub(r"[^A-Za-z0-9_-]+", "-", handle).strip("-")
    return handle or "user"


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


def _gmail_test(svc):
    return svc.users().getProfile(userId="me").execute().get("emailAddress")


def _calendar_test(svc):
    return svc.calendars().get(calendarId="primary").execute().get("id")


def _drive_test(svc):
    return svc.about().get(fields="user").execute().get("user", {}).get("emailAddress")


NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


SERVICES = {
    # ── Google (oauth_browser) ────────────────────────────────────────────
    "google-gmail": {
        "provider": "google",
        "launcher": "npx",
        "auth_kind": "oauth_browser",
        "label": "Gmail",
        "short": "Gmail",
        "service_name": "Gmail",
        "api_id": "gmail.googleapis.com",
        "api_url": "https://console.cloud.google.com/apis/library/gmail.googleapis.com",
        "scopes": [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.settings.basic",
        ],
        "scopes_note": (
            "gmail.modify = read + send + label/draft management\n"
            "gmail.settings.basic = filters / forwarding / vacation responder"
        ),
        "npx_package": "@gongrzhe/server-gmail-autoauth-mcp",
        "env_credentials_var": "GMAIL_OAUTH_PATH",
        "env_token_var": "GMAIL_CREDENTIALS_PATH",
        "token_filename": "credentials.json",
        "test_service_name": "gmail",
        "test_api_version": "v1",
        "test_call": _gmail_test,
    },
    "google-calendar": {
        "provider": "google",
        "launcher": "npx",
        "auth_kind": "oauth_browser",
        "label": "Google Calendar",
        "short": "Calendar",
        "service_name": "GoogleCalendar",
        "api_id": "calendar-json.googleapis.com",
        "api_url": "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "scopes_note": (
            "calendar = full read + write on all calendars the account owns "
            "or has been shared into"
        ),
        "npx_package": "@cocal/google-calendar-mcp",
        "env_credentials_var": "GOOGLE_OAUTH_CREDENTIALS",
        "env_token_var": "GOOGLE_CALENDAR_MCP_TOKEN_PATH",
        "token_filename": "tokens.json",
        "test_service_name": "calendar",
        "test_api_version": "v3",
        "test_call": _calendar_test,
    },
    "google-drive": {
        "provider": "google",
        "launcher": "npx",
        "auth_kind": "oauth_browser",
        "label": "Google Drive",
        "short": "Drive",
        "service_name": "GoogleDrive",
        "api_id": "drive.googleapis.com",
        "api_url": "https://console.cloud.google.com/apis/library/drive.googleapis.com",
        "scopes": ["https://www.googleapis.com/auth/drive"],
        "scopes_note": (
            "drive = full read + write across the entire Drive.\n"
            "Alternatives if you want a smaller blast-radius: drive.readonly "
            "(read-only) or drive.file (only files this MCP creates)."
        ),
        "npx_package": "@piotr-agier/google-drive-mcp",
        "env_credentials_var": "GOOGLE_DRIVE_OAUTH_CREDENTIALS",
        "env_token_var": "GOOGLE_DRIVE_MCP_TOKEN_PATH",
        "token_filename": "tokens.json",
        "test_service_name": "drive",
        "test_api_version": "v3",
        "test_call": _drive_test,
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
                "description": "Optional Confluence base URL (e.g. https://confluence.company.com or .../wiki). Leave blank to skip Confluence.",
            },
            {
                "name": "CONFLUENCE_PERSONAL_TOKEN",
                "required": False,
                "validator": "nonempty_or_empty",
                "description": "Optional Confluence DC PAT. Leave blank to skip.",
                "secret": True,
                "skip_if_blank": "CONFLUENCE_URL",
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
# Cloud Console step-printer (Google only)
# ──────────────────────────────────────────────────────────────────────────────

def print_cloud_steps(service_key):
    s = SERVICES[service_key]
    step(1, f"Google Cloud Console — set up the OAuth client for {s['label']}")
    print(f"""
  1a. Open <https://console.cloud.google.com/projectcreate> and create a project
      (or select an existing personal project). Note the project_id — you'll
      see it in the project picker at the top of every Console page.

  1b. Enable the {s['label']} API:
        {s['api_url']}
      Click "Enable" at the top of that page (no billing required for personal use).

  1c. Configure the OAuth consent screen:
      <https://console.cloud.google.com/apis/credentials/consent>
        • User Type:           External
        • App name:            anything ("Personal MCP" works)
        • User support email:  the Google account you'll authenticate with
        • Developer contact:   same email
      Add this scope under "Scopes for Google APIs":
{chr(10).join(f"        {sc}" for sc in s['scopes'])}
      Note: {s['scopes_note']}

  1d. Add your Google account as a Test User on the consent screen.
      (Publishing status = "Testing" is fine for personal use. Refresh tokens
       last 7 days in testing mode — re-auth via this script if expired.)

  1e. Create the OAuth 2.0 Client ID:
      <https://console.cloud.google.com/apis/credentials>
        • Click "Create Credentials" → "OAuth client ID"
        • Application type:  Desktop app
        • Name:              anything
      Click "DOWNLOAD JSON" on the resulting client.
      You'll get a file like `client_secret_<id>.apps.googleusercontent.com.json`.
""")
    input("  Press Enter once you have the JSON file downloaded… ")


# ──────────────────────────────────────────────────────────────────────────────
# Credentials & state-dir handling
# ──────────────────────────────────────────────────────────────────────────────

def validate_oauth_keys(path_str):
    path = Path(path_str).expanduser()
    if not path.exists():
        return False, f"file not found: {path}"
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return False, f"not valid JSON: {e}"
    if "installed" not in data and "web" not in data:
        return False, "expected Google OAuth installed/web app format ({\"installed\": {...}})"
    return True, None


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def email_to_safe_part(email):
    return re.sub(r"[^A-Za-z0-9]+", "-", email).strip("-")


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
# Delegated OAuth via the vendor's own auth subcommand
# ──────────────────────────────────────────────────────────────────────────────

def run_vendor_auth(service_key, creds_path, token_path):
    s = SERVICES[service_key]
    env = os.environ.copy()
    env[s["env_credentials_var"]] = str(creds_path)
    env[s["env_token_var"]] = str(token_path)
    cwd = creds_path.parent
    cmd = ["npx", "-y", s["npx_package"], "auth"]
    step(3, f"OAuth flow — delegating to vendor `{s['npx_package']} auth`")
    print(f"  cwd: {cwd}")
    print(f"  env: {s['env_credentials_var']}={creds_path}")
    print(f"       {s['env_token_var']}={token_path}")
    if _under_snap():
        print()
        print("  ⚠ SNAP-CONFINED ENVIRONMENT DETECTED (e.g. snap-installed VS Code).")
        print("     The vendor will try to auto-open your browser via xdg-open, which")
        print("     fails silently inside a snap sandbox. Watch the vendor output for")
        print("     a line starting with 'If the browser doesn't open, visit:' — copy")
        print("     that URL into a normal browser window OUTSIDE the snap'd app.")
    print()
    print("  Your browser should open. Sign in with the Google account whose")
    print("  Drive/Calendar/Gmail you want this MCP server to access. Approve")
    print("  the scopes you set on the consent screen.")
    print()
    print("  If the browser does NOT open within ~5 seconds, watch the vendor")
    print("  output below for the fallback URL — copy/paste it manually.")
    print()
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env)
    except KeyboardInterrupt:
        print()
        print("  Interrupted. The vendor auth process was killed.")
        print("  Re-run this script when you're ready to retry.")
        return False
    if result.returncode != 0:
        print(f"\n  ✗ Vendor auth subcommand failed with exit code {result.returncode}.")
        print("  Common causes:")
        print("   - 'Access blocked: <App> has not completed the Google verification process'")
        print("     → Add your email as Test User on the OAuth consent screen.")
        print("   - 'redirect_uri_mismatch' → confirm the OAuth client is Desktop App type.")
        print("   - Browser didn't open AND you're inside a snap sandbox → use the")
        print("     fallback URL the vendor printed, in a NON-snap'd browser window.")
        return False
    return True


def _under_snap():
    return any(os.environ.get(k) for k in ("SNAP", "SNAP_NAME", "SNAP_INSTANCE_NAME"))


# ──────────────────────────────────────────────────────────────────────────────
# Connection test (best-effort, Google-only)
# ──────────────────────────────────────────────────────────────────────────────

def test_connection(service_key, token_path):
    s = SERVICES[service_key]
    step(4, "Connection test — calling the service's identity endpoint")
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        print("  ⚠  google-api-python-client not available — skipping API test.")
        print("     (Run via `uv run --script` so dependencies auto-install,")
        print("      or pip install google-api-python-client google-auth.)")
        return None

    if not token_path.exists():
        print(f"  ⚠  token file not found at {token_path} — vendor may use a different path.")
        print("     Skipping API test; the next session-load will reveal whether auth worked.")
        return None

    try:
        raw = json.loads(token_path.read_text())
    except Exception as e:
        print(f"  ⚠  could not parse token file: {e} — skipping API test.")
        return None

    if isinstance(raw, dict) and not raw.get("token") and not raw.get("access_token"):
        for k in raw.keys():
            if isinstance(raw[k], dict) and ("access_token" in raw[k] or "refresh_token" in raw[k]):
                raw = raw[k]
                break

    try:
        creds = Credentials(
            token=raw.get("token") or raw.get("access_token"),
            refresh_token=raw.get("refresh_token"),
            token_uri=raw.get("token_uri") or "https://oauth2.googleapis.com/token",
            client_id=raw.get("client_id"),
            client_secret=raw.get("client_secret"),
            scopes=raw.get("scopes") or raw.get("scope", "").split() if isinstance(raw.get("scope"), str) else raw.get("scopes"),
        )
        svc = build(s["test_service_name"], s["test_api_version"], credentials=creds, cache_discovery=False)
        identity = s["test_call"](svc)
        print(f"  ✓ API call returned: {identity}")
        return identity
    except Exception as e:
        print(f"  ⚠  API test failed: {e}")
        print("     (auth may still have worked — the failure could be a token-format")
        print("      mismatch on our side, not the vendor's. Proceeding to registration.)")
        return None


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
        ["claude", "mcp", "remove", "--scope", "user", title],
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
    cmd = ["claude", "mcp", "add", "--scope", "user", title]
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
# Setup — oauth_browser branch (Google services)
# ──────────────────────────────────────────────────────────────────────────────

def _setup_oauth_browser(service_key, s):
    print_cloud_steps(service_key)

    step(2, "Locate the OAuth credentials JSON you just downloaded")
    print(f"  Default glob: ~/Downloads/client_secret_*.json")
    default = _glob_latest_credentials() or ""
    creds_input = prompt(
        "Path to credentials JSON",
        default=default if default else None,
        validator=validate_oauth_keys,
    )
    creds_src = Path(creds_input).expanduser().resolve()

    tmp_slug = "_pending"
    state_dir = make_state_dir(service_key, tmp_slug)
    creds_path = state_dir / "gcp-oauth.keys.json"
    shutil.copy2(creds_src, creds_path)
    token_path = state_dir / s["token_filename"]
    print(f"  ✓ creds staged at {creds_path}")

    ok = run_vendor_auth(service_key, creds_path, token_path)
    if not ok:
        print(f"\n  Setup aborted. State preserved at {state_dir} for inspection.")
        return 1

    detected = test_connection(service_key, token_path)
    if detected and EMAIL_RE.match(detected):
        email = detected
        save_cached_email(email)
    else:
        cached = load_cached_email()
        if cached:
            email = cached
            print(f"  ⓘ Reusing cached account email from previous setup: {email}")
        else:
            email = prompt(
                "Could not auto-detect email from token. Enter it manually",
                validator=lambda e: (EMAIL_RE.match(e) is not None, "looks invalid"),
            )
            save_cached_email(email)

    step(5, "Pick a title for this MCP server in Claude Code")
    print("  Note: claude mcp names accept letters, numbers, hyphens, and underscores only.")
    print("  Default is <email-handle>-<ServiceName> (e.g. minaalfy8-GoogleDrive).")
    default_title = f"{email_handle(email)}-{s['service_name']}"
    title = prompt("Title", default=default_title, validator=validate_mcp_name)
    final_slug = slugify(title)
    final_dir = STATE_ROOT / service_key / final_slug
    replacing = final_dir.exists() and final_dir != state_dir
    if replacing:
        print(f"  ⓘ State dir already exists for slug '{final_slug}': {final_dir}")
        print("     This usually means re-authenticating the same account+service.")
        if not confirm("Replace existing state with the freshly authenticated tokens?", default=True):
            print("     Pick a different title or remove the existing state dir first.")
            return 1
        shutil.rmtree(final_dir)
        print(f"  ✓ removed old state dir")
    if final_dir != state_dir:
        state_dir.rename(final_dir)
        creds_path = final_dir / "gcp-oauth.keys.json"
        token_path = final_dir / s["token_filename"]
        print(f"  ✓ state moved to {final_dir}")

    if replacing:
        subprocess.run(
            ["claude", "mcp", "remove", "--scope", "user", title],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    env_pairs = {
        s["env_credentials_var"]: str(creds_path),
        s["env_token_var"]: str(token_path),
    }
    ok = claude_mcp_add(
        title=title,
        launcher=s["launcher"],
        package=s["npx_package"],
        env_pairs=env_pairs,
    )
    if not ok:
        return 1

    if not _finalize_with_handshake(
        title=title, final_dir=final_dir,
        launcher=s["launcher"], package=s["npx_package"], env_pairs=env_pairs,
        step_num=7,
    ):
        return 1

    hr("═")
    print(f"  ✓ Done. '{title}' is registered and verified.")
    print(f"    State:  {final_dir}")
    print(f"    Run `claude mcp list` to confirm and restart your session.")
    hr("═")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Setup — api_token branch (Atlassian DC, Tempo)
# ──────────────────────────────────────────────────────────────────────────────

def _setup_api_token(service_key, s):
    step(1, f"Generate a Personal Access Token for {s['label']}")
    print(f"""
  How to create a Jira DC Personal Access Token:
    1. Sign in to your Jira instance in a browser.
    2. Open your profile menu (top-right avatar) → "Personal Access Tokens"
       (Data Center / Server only — Cloud uses API tokens instead).
    3. Click "Create token", give it a name (e.g. "Claude Code MCP"),
       set expiry as your security policy requires, and copy the token.
    4. Paste it below when prompted.

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
        marker = "" if required else "  (optional, leave blank to skip)"
        print(f"\n  {name}{marker}")
        print(f"    {spec['description']}")
        v = prompt(
            name,
            default=spec.get("default"),
            validator=validator,
            allow_empty=not required,
            secret=spec.get("secret", False),
        )
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
            ["claude", "mcp", "remove", "--scope", "user", title],
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
            ["claude", "mcp", "remove", "--scope", "user", title],
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
            ["claude", "mcp", "remove", "--scope", "user", title],
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
    cmd = ["claude", "mcp", "add", "--transport", "http", "--scope", "user", title, url]
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
        ["claude", "mcp", "remove", "--scope", "user", title],
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
            ["claude", "mcp", "remove", "--scope", "user", title],
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

def cmd_setup(service_key):
    if service_key not in SERVICES:
        print(f"unknown service: {service_key}; expected one of {list(SERVICES)}")
        return 2
    s = SERVICES[service_key]

    print()
    hr("═")
    print(f"  {s['label']} MCP setup")
    hr("═")

    if s["auth_kind"] == "oauth_browser":
        return _setup_oauth_browser(service_key, s)
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


def _glob_latest_credentials():
    import glob
    matches = sorted(
        glob.glob(str(Path.home() / "Downloads" / "client_secret_*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    return matches[0] if matches else None


# ──────────────────────────────────────────────────────────────────────────────
# Doctor
# ──────────────────────────────────────────────────────────────────────────────

def cmd_doctor():
    print("\n=== Registered MCP servers (per `claude mcp list`) ===")
    subprocess.run(["claude", "mcp", "list"])
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
            files = sorted(f.name for f in slug_dir.iterdir())
            print(f"    {slug_dir.name}/  {files}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        prog="setup-mcp.py",
        description="Set up MCP servers (Google, Atlassian, Tempo, LinkedIn, Azure DevOps) for Claude Code.",
    )
    sub = ap.add_subparsers(dest="service", required=True)
    for key, cfg in SERVICES.items():
        sub.add_parser(key, help=f"Set up the {cfg['label']} MCP server.")
    sub.add_parser("doctor", help="List registered servers and their local state.")
    # alias: `linkedin` works as a positional even though the dict key matches.
    args = ap.parse_args()
    if args.service == "doctor":
        return cmd_doctor()
    return cmd_setup(args.service)


if __name__ == "__main__":
    sys.exit(main())

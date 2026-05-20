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
setup-mcp.py — interactive setup for MCP servers (Google + Atlassian + Tempo).

Usage:
    ./setup-mcp.py google-gmail
    ./setup-mcp.py google-calendar
    ./setup-mcp.py google-drive
    ./setup-mcp.py atlassian-sooperset
    ./setup-mcp.py tempo-filler
    ./setup-mcp.py doctor            # list registered MCPs + their state

Or use the wrapper scripts under ./bin/.

Two auth flavours are supported:

  • oauth_browser  (Google services)
    1. Prints the Google Cloud Console steps you need to do manually.
    2. Waits for you to drop the credentials JSON into the path you'll point it at.
    3. Delegates OAuth to the vendor's own `auth` subcommand.
    4. Best-effort connection test against the service's identity endpoint.
    5. Prompts for a server title (defaults to `<email-handle>-<ServiceName>`).
    6. Registers via `claude mcp add --scope user -- npx -y <pkg>`.

  • api_token  (Atlassian Data Center, Tempo Server)
    1. Prints where to generate the PAT in your Jira profile.
    2. Prompts for each required/optional env var.
    3. Writes `state/<svc>/<slug>/env` (mode 600) for rotation/inspection.
    4. Title default is `<host-slug>-<ServiceName>` (derived from the URL you entered).
    5. Registers via `claude mcp add --scope user --env KEY=VAL ... -- <launcher> <pkg>`.

All persistent state (oauth keys + tokens + env files) lives under ./state/<svc>/<slug>/
so the whole toolkit stays portable. The repo's .gitignore excludes ./state/.
"""

import argparse
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


VALIDATORS = {
    "url": _v_url,
    "url_or_empty": _v_url_or_empty,
    "nonempty": _v_nonempty,
    "nonempty_or_empty": _v_nonempty_or_empty,
    "int_or_empty": _v_int_or_empty,
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

def claude_mcp_add(title, launcher, package, env_pairs, step_num=6):
    cmd = ["claude", "mcp", "add", "--scope", "user", title]
    for k, v in env_pairs.items():
        if v == "":
            continue
        cmd += ["--env", f"{k}={v}"]
    cmd += ["--"]
    if launcher == "npx":
        cmd += ["npx", "-y", package]
    elif launcher == "uvx":
        cmd += ["uvx", package]
    else:
        print(f"  ✗ Unknown launcher: {launcher}")
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
    ok = claude_mcp_add(
        title=title,
        launcher=s["launcher"],
        package=s["npx_package"],
        env_pairs={
            s["env_credentials_var"]: str(creds_path),
            s["env_token_var"]: str(token_path),
        },
    )
    if not ok:
        return 1

    hr("═")
    print(f"  ✓ Done. '{title}' is registered.")
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
    host_slug = host_from_url(source_value) if source_value else "host"
    print(f"  Default is <host-slug>-{s['service_name']} (e.g. {host_slug}-{s['service_name']}).")
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

    hr("═")
    print(f"  ✓ Done. '{title}' is registered.")
    print(f"    State:  {final_dir}")
    print(f"    Rotate the PAT later by re-running this command with the same title.")
    print(f"    Run `claude mcp list` to confirm and restart your session.")
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
        description="Set up MCP servers (Google + Atlassian + Tempo) for Claude Code.",
    )
    sub = ap.add_subparsers(dest="service", required=True)
    for key, cfg in SERVICES.items():
        sub.add_parser(key, help=f"Set up the {cfg['label']} MCP server.")
    sub.add_parser("doctor", help="List registered servers and their local state.")
    args = ap.parse_args()
    if args.service == "doctor":
        return cmd_doctor()
    return cmd_setup(args.service)


if __name__ == "__main__":
    sys.exit(main())

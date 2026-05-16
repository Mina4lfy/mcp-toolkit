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
setup-google-mcp.py — interactive setup for Google MCP servers.

Usage:
    ./setup-google-mcp.py gmail
    ./setup-google-mcp.py calendar
    ./setup-google-mcp.py drive
    ./setup-google-mcp.py doctor      # list registered Google MCPs + their state

Or use the wrapper scripts under ./bin/.

Per service the script:
  1. Prints the Google Cloud Console steps you need to do manually.
  2. Waits for you to drop the credentials JSON into the path you'll point it at.
  3. Delegates OAuth to the vendor's own `auth` subcommand (each vendor knows
     how to write its own token file format correctly).
  4. Best-effort connection test against the service's identity endpoint.
  5. Prompts for a server title (defaults to `{email} - {Service}`).
  6. Registers the server with Claude Code via `claude mcp add --scope user`.

All persistent state (oauth keys + token files) lives under ./state/<slug>/
so the whole toolkit stays portable. The brain repo's main .gitignore should
exclude mcp-toolkit/state/.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
STATE_ROOT = REPO_ROOT / "state"


def _gmail_test(svc):
    return svc.users().getProfile(userId="me").execute().get("emailAddress")


def _calendar_test(svc):
    return svc.calendars().get(calendarId="primary").execute().get("id")


def _drive_test(svc):
    return svc.about().get(fields="user").execute().get("user", {}).get("emailAddress")


SERVICES = {
    "gmail": {
        "label": "Gmail",
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
    "calendar": {
        "label": "Google Calendar",
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
    "drive": {
        "label": "Google Drive",
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


def prompt(question, default=None, validator=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        answer = input(f"  {question}{suffix}: ").strip()
        if not answer and default is not None:
            answer = default
        if not answer:
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
# Cloud Console step-printer
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


def make_state_dir(service_key, slug):
    d = STATE_ROOT / service_key / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# ──────────────────────────────────────────────────────────────────────────────
# Delegated OAuth via the vendor's own auth subcommand
# ──────────────────────────────────────────────────────────────────────────────

def run_vendor_auth(service_key, creds_path, token_path):
    s = SERVICES[service_key]
    env = os.environ.copy()
    env[s["env_credentials_var"]] = str(creds_path)
    env[s["env_token_var"]] = str(token_path)
    # Gmail's vendor also looks at the cwd for gcp-oauth.keys.json — set cwd
    # to the creds dir so the legacy path works if env var is ignored.
    cwd = creds_path.parent
    cmd = ["npx", "-y", s["npx_package"], "auth"]
    step(3, f"OAuth flow — delegating to vendor `{s['npx_package']} auth`")
    print(f"  cwd: {cwd}")
    print(f"  env: {s['env_credentials_var']}={creds_path}")
    print(f"       {s['env_token_var']}={token_path}")
    print("\n  Your browser should open. Sign in with the Google account whose")
    print("  Drive/Calendar/Gmail you want this MCP server to access. Approve")
    print("  the scopes you set on the consent screen.\n")
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        print(f"\n  ✗ Vendor auth subcommand failed with exit code {result.returncode}.")
        print("  Common causes:")
        print("   - 'Access blocked: <App> has not completed the Google verification process'")
        print("     → Add your email as Test User on the OAuth consent screen.")
        print("   - 'redirect_uri_mismatch' → confirm the OAuth client is Desktop App type.")
        print("   - Browser didn't open → run this on a machine with a desktop env.")
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Connection test (best-effort)
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

    # Some vendors (calendar) wrap tokens under an account key like "normal".
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

def claude_mcp_add(title, npx_package, env_pairs):
    cmd = [
        "claude", "mcp", "add",
        "--scope", "user",
        title,
    ]
    for k, v in env_pairs.items():
        cmd += ["--env", f"{k}={v}"]
    cmd += ["--", "npx", "-y", npx_package]
    step(6, f"Registering with Claude Code as '{title}'")
    print("  command:")
    print("    " + " ".join(_shellquote(c) for c in cmd))
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

    print_cloud_steps(service_key)

    # STEP 2 — pick credentials JSON
    step(2, "Locate the OAuth credentials JSON you just downloaded")
    print(f"  Default glob: ~/Downloads/client_secret_*.json")
    default = _glob_latest_credentials() or ""
    creds_input = prompt(
        "Path to credentials JSON",
        default=default if default else None,
        validator=validate_oauth_keys,
    )
    creds_src = Path(creds_input).expanduser().resolve()

    # Stage state under a temporary slug; rename after we have the title.
    tmp_slug = "_pending"
    state_dir = make_state_dir(service_key, tmp_slug)
    creds_path = state_dir / "gcp-oauth.keys.json"
    shutil.copy2(creds_src, creds_path)
    token_path = state_dir / s["token_filename"]
    print(f"  ✓ creds staged at {creds_path}")

    # STEP 3 — delegated OAuth
    ok = run_vendor_auth(service_key, creds_path, token_path)
    if not ok:
        print(f"\n  Setup aborted. State preserved at {state_dir} for inspection.")
        return 1

    # STEP 4 — connection test (best-effort)
    email = test_connection(service_key, token_path)
    if not email:
        email = prompt("Could not auto-detect email from token. Enter it manually", validator=lambda e: (re.match(r"^[^@]+@[^@]+\.[^@]+$", e) is not None, "looks invalid"))

    # STEP 5 — title prompt
    step(5, "Pick a title for this MCP server in Claude Code")
    default_title = f"{email} - {s['label']}"
    title = prompt("Title", default=default_title)
    final_slug = slugify(title)
    final_dir = STATE_ROOT / service_key / final_slug
    if final_dir.exists() and final_dir != state_dir:
        print(f"  ✗ State dir already exists for slug '{final_slug}': {final_dir}")
        print("     Pick a different title or remove the existing state dir first.")
        return 1
    if final_dir != state_dir:
        state_dir.rename(final_dir)
        creds_path = final_dir / "gcp-oauth.keys.json"
        token_path = final_dir / s["token_filename"]
        print(f"  ✓ state moved to {final_dir}")

    # STEP 6 — claude mcp add
    ok = claude_mcp_add(
        title=title,
        npx_package=s["npx_package"],
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
    print("\n=== Registered Google MCP servers (per `claude mcp list`) ===")
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
    ap = argparse.ArgumentParser(prog="setup-google-mcp.py", description="Set up Google MCP servers for Claude Code.")
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

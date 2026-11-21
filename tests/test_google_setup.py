import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_SPEC = importlib.util.spec_from_file_location("setup_mcp", Path(__file__).resolve().parent.parent / "setup-mcp.py")
setup_mcp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(setup_mcp)

HANDLE = "acct"
ALL_APPS = ["calendar", "drive", "gmail"]


class GoogleSetupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_root = Path(self.tmp.name)
        self.service = setup_mcp.SERVICES["google"]
        self.claude_calls = []
        self._patch(setup_mcp, "STATE_ROOT", self.state_root)
        self._patch(setup_mcp, "claude_cmd", lambda *a: ["claude", *a])
        self._patch(setup_mcp, "confirm", lambda question, default=True: default)
        self._patch(setup_mcp.subprocess, "run", self._fake_run)
        self._patch(setup_mcp, "_ensure_undici_shim", lambda: None)
        self._patch(setup_mcp, "_ensure_mcp_timeout", lambda *a, **k: None)

    def _patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def _fake_run(self, cmd, *a, **k):
        self.claude_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def register(self, apps):
        for app in apps:
            d = self.state_root / "google" / HANDLE / app
            d.mkdir(parents=True)
            (d / "gcp-oauth.keys.json").write_text("{}")
            (d / self.service["apps"][app]["token_file"]).write_text("{}")
        setup_mcp._save_google_state("google", HANDLE, "id.apps.googleusercontent.com", "GOCSPX-secret", "", apps)

    def run_setup(self, apps=None, inputs=()):
        args = SimpleNamespace(client_secret=None, handle=HANDLE, apps=apps)
        with mock.patch("builtins.input", side_effect=list(inputs)):
            return setup_mcp._setup_local_oauth("google", self.service, args)

    def removed_titles(self):
        return [c[-1] for c in self.claude_calls if c[1:3] == ["mcp", "remove"]]


class RemoveEveryApp(GoogleSetupCase):
    def assert_all_removed(self, rc):
        self.assertEqual(rc, 0)
        self.assertEqual(
            sorted(self.removed_titles()), sorted(f"{HANDLE}-{t}" for t in ("Gmail", "GoogleDrive", "GoogleCalendar"))
        )
        self.assertEqual(setup_mcp._load_google_state("google", HANDLE)["apps"], [])
        for app in ALL_APPS:
            self.assertFalse((self.state_root / "google" / HANDLE / app).exists())

    def test_apps_none_flag_removes_every_app(self):
        self.register(ALL_APPS)
        self.assert_all_removed(self.run_setup(apps="none"))

    def test_empty_interactive_selection_removes_every_app(self):
        self.register(ALL_APPS)
        self.assert_all_removed(self.run_setup(inputs=["none", ""]))

    def test_empty_selection_on_a_new_account_still_asks_again(self):
        with mock.patch("builtins.input", side_effect=["none", "", "2", ""]):
            picked = setup_mcp.prompt_multiselect("pick", [("a", "A"), ("b", "B")])
        self.assertEqual(picked, ["b"])


class RerunRepairsRegistrations(GoogleSetupCase):
    def added(self):
        return {c[5]: c for c in self.claude_calls if c[1:3] == ["mcp", "add"]}

    def auth_runs(self):
        return [c for c in self.claude_calls if c[0] == "npx" and c[-1] == "auth"]

    def test_unchanged_selection_registers_every_kept_app_with_current_paths(self):
        self.register(ALL_APPS)
        self.assertEqual(self.run_setup(apps="gmail,drive,calendar"), 0)
        added = self.added()
        self.assertEqual(sorted(added), sorted(f"{HANDLE}-{t}" for t in ("Gmail", "GoogleDrive", "GoogleCalendar")))
        gmail_dir = self.state_root / "google" / HANDLE / "gmail"
        self.assertIn(f"GMAIL_CREDENTIALS_PATH={gmail_dir / 'credentials.json'}", added[f"{HANDLE}-Gmail"])
        self.assertEqual(self.auth_runs(), [])

    def test_kept_app_without_a_token_is_authorized_again(self):
        self.register(ALL_APPS)
        (self.state_root / "google" / HANDLE / "drive" / "tokens.json").unlink()
        self.run_setup(apps="gmail,drive,calendar")
        self.assertEqual([c[2] for c in self.auth_runs()], [self.service["apps"]["drive"]["package"]])


class ConsentPorts(GoogleSetupCase):
    def run_auth(self, app_key, busy, out):
        app = self.service["apps"][app_key]
        d = self.state_root / app_key
        d.mkdir()
        self._patch(setup_mcp, "_port_busy", lambda port: port in busy)
        self._patch(setup_mcp, "_port_holder", lambda port: "node (pid 42)")
        with mock.patch("sys.stdout", out):
            return setup_mcp._run_google_app_auth(app, d / "gcp-oauth.keys.json", d / app["token_file"], d, None)

    def npx_calls(self):
        return [c for c in self.claude_calls if c[0] == "npx"]

    def test_gmail_consent_is_refused_when_its_only_port_is_busy(self):
        out = io.StringIO()
        self.assertFalse(self.run_auth("gmail", {3000}, out))
        self.assertEqual(self.npx_calls(), [])
        self.assertIn("node (pid 42)", out.getvalue())

    def test_drive_reports_the_port_it_falls_through_to(self):
        out = io.StringIO()
        self.run_auth("drive", {3000}, out)
        self.assertEqual(len(self.npx_calls()), 1)
        self.assertIn("port 3001", out.getvalue())

    def test_failed_consent_leaves_no_keys_file(self):
        self._patch(setup_mcp, "_run_google_app_auth", lambda *a: False)
        self.register(["drive", "calendar"])
        self.run_setup(apps="gmail,drive,calendar", inputs=[""])
        self.assertFalse((self.state_root / "google" / HANDLE / "gmail").exists())


class TestingModeTokens(GoogleSetupCase):
    def rerun(self, token_body):
        self.register(["calendar"])
        tok = self.state_root / "google" / HANDLE / "calendar" / "tokens.json"
        tok.write_text(token_body)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.run_setup(apps="calendar")
        return out.getvalue()

    def test_reused_testing_mode_token_is_flagged(self):
        out = self.rerun('{"normal": {"refresh_token": "x", "refresh_token_expires_in": 604799}}')
        self.assertIn("Testing-mode token", out)

    def test_production_token_is_not_flagged(self):
        out = self.rerun('{"normal": {"refresh_token": "x", "expiry_date": 1}}')
        self.assertNotIn("Testing-mode token", out)


if __name__ == "__main__":
    unittest.main()

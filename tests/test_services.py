import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("setup_mcp", ROOT / "setup-mcp.py")
setup_mcp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(setup_mcp)


class EveryServiceCanLaunch(unittest.TestCase):
    def test_no_service_launches_from_a_local_dir_missing_from_the_repo(self):
        for key, s in setup_mcp.SERVICES.items():
            if "local_pkg_dir" in s:
                self.assertTrue((ROOT / s["local_pkg_dir"]).is_dir(), key)

    def test_every_auth_kind_has_a_setup_branch(self):
        handled = {"local_oauth", "api_token", "entra_login", "remote_http"}
        for key, s in setup_mcp.SERVICES.items():
            self.assertIn(s["auth_kind"], handled, key)

    def test_every_documented_wrapper_exists(self):
        for line in (ROOT / "README.md").read_text().splitlines():
            if line.lstrip().startswith("./bin/"):
                self.assertTrue((ROOT / line.split()[0]).is_file(), line)


if __name__ == "__main__":
    unittest.main()

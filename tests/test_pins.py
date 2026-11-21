import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("setup_mcp", ROOT / "setup-mcp.py")
setup_mcp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(setup_mcp)

EXACT = re.compile(r"^(@[^/@]+/)?[^@/]+@\d+\.\d+\.\d+$")


def launched_packages():
    for key, s in setup_mcp.SERVICES.items():
        if s["launcher"] not in ("npx", "uvx"):
            continue
        for pkg in (s.get("package"), s.get("npx_package")):
            if pkg:
                yield key, s, pkg
        for app in s.get("apps", {}).values():
            yield key, s, app["package"]


class EveryLaunchIsPinned(unittest.TestCase):
    def test_every_launched_package_names_an_exact_version(self):
        found = list(launched_packages())
        self.assertGreaterEqual(len(found), 8)
        for key, _, pkg in found:
            self.assertRegex(pkg, EXACT, key)

    def test_pinned_version_matches_the_cited_vendor_release(self):
        for key, s, pkg in launched_packages():
            cited = re.search(r"Source pin: vendor/\S+ @ [0-9a-f]{7,} \(v(\d+\.\d+\.\d+)\)",
                              s.get("scopes_note", ""))
            if cited:
                self.assertEqual(pkg.rsplit("@", 1)[1], cited.group(1), key)

    def test_spawn_command_keeps_the_pin(self):
        self.assertEqual(setup_mcp._build_spawn_command("npx", "@a/b@1.2.3"),
                         ["npx", "-y", "@a/b@1.2.3"])
        self.assertEqual(setup_mcp._build_spawn_command("uvx", "pkg@1.2.3"),
                         ["uvx", "pkg@1.2.3"])


if __name__ == "__main__":
    unittest.main()

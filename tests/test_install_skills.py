import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_skills.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


class InstallSkillsTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("--dest", tmp, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertIn("fleet-wait", result.stdout)

    def test_install_rewrites_plugin_root_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run("--dest", tmp).returncode, 0)
            text = (Path(tmp) / "fleet-wait" / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("<PLUGIN_ROOT>", text)
            self.assertNotIn("](../../", text)
            self.assertIn(ROOT.as_posix() + "/scripts/fleet.py", text)
            again = run("--dest", tmp)
            self.assertEqual(again.returncode, 1)
            self.assertIn("--force", again.stderr)
            self.assertEqual(run("--dest", tmp, "--force").returncode, 0)

    def test_manifests_are_valid_json_and_skills_have_frontmatter(self):
        for name in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json", "plugin.json"):
            json.loads((ROOT / name).read_text(encoding="utf-8"))
        for skill in (ROOT / "skills").iterdir():
            head = (skill / "SKILL.md").read_text(encoding="utf-8").splitlines()[:4]
            self.assertEqual(head[0], "---", skill.name)
            self.assertTrue(any(line.startswith("name: " + skill.name) for line in head), skill.name)


if __name__ == "__main__":
    unittest.main()

"""Package sanity: manifests agree and every skill has valid frontmatter."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ['plan', 'implement', 'review', 'release', 'status', 'wait']


class PackageTests(unittest.TestCase):
    def test_manifests_agree(self):
        a = json.loads((ROOT / 'plugin.json').read_text())
        b = json.loads((ROOT / '.codex-plugin/plugin.json').read_text())
        self.assertEqual((a['name'], a['version']), (b['name'], b['version']))

    def test_skills(self):
        found = sorted(p.name for p in (ROOT / 'skills').iterdir() if p.is_dir())
        self.assertEqual(found, sorted('fleet-' + s for s in SKILLS))
        for name in found:
            text = (ROOT / 'skills' / name / 'SKILL.md').read_text(encoding='utf-8')
            self.assertTrue(text.startswith('---\nname: ' + name + '\n'), name)
            self.assertIn('description:', text.split('---')[1])

    def test_doctor_and_help(self):
        cli = str(ROOT / 'scripts/fleet.py')
        r = subprocess.run([sys.executable, cli, 'doctor'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('git', r.stdout)
        r = subprocess.run([sys.executable, cli, '--help'], capture_output=True, text=True)
        self.assertIn('doctor', r.stdout)


if __name__ == '__main__':
    unittest.main()

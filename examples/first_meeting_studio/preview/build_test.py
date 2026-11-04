"""Build/entry contract tests; platform execution additionally needs that OS."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

class BuildTests(unittest.TestCase):
    def test_output_and_portable_entry_contract(self):
        for name in ['build.ps1', 'build.sh']:
            self.assertIn('build.py', (HERE / name).read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'offline.html'
            result = subprocess.run([sys.executable, str(HERE / 'build.py'), '--output', str(output)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding='utf-8')
            self.assertNotIn('/*__', html)
            self.assertNotIn('https://', html)
            self.assertNotIn('http://', html)
            self.assertEqual(html.count('data:image/png;base64,'), 79)
    def test_bad_argument_fails(self):
        result = subprocess.run([sys.executable, str(HERE / 'build.py'), '--unknown'], capture_output=True)
        self.assertEqual(result.returncode, 2)

if __name__ == '__main__':
    unittest.main()

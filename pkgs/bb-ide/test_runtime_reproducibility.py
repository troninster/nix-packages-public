import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def workflow_step(source, name):
    start = source.index(f"      - name: {name}\n")
    end = source.find("\n      - name: ", start + 1)
    return source[start:end]

class PnpmRuntimeReproducibilityTests(unittest.TestCase):
    def test_runtime_metadata_removal_preserves_dependency_links(self):
        for package, runtime in (("bb-ide", "share/bb/runtime"), ("symphony-ts", "lib/symphony-ts")):
            with self.subTest(package=package), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                modules = output / runtime / "node_modules"
                modules.mkdir(parents=True)
                for name in (".modules.yaml", ".pnpm-workspace-state-v1.json"):
                    (modules / name).write_text("volatile installer state")
                dependency = modules / ".pnpm" / "dependency" / "index.js"
                dependency.parent.mkdir(parents=True)
                dependency.write_text("module.exports = 1")
                (modules / "dependency").symlink_to(".pnpm/dependency")
                recipe = (ROOT / "pkgs" / package / "default.nix").read_text()
                cleanup = re.search(r'    rm -f .*?\.pnpm-workspace-state-v1\.json"', recipe, re.S).group(0)
                subprocess.run(["bash", "-eu", "-c", cleanup], env={**os.environ, "out": temp}, check=True)
                self.assertFalse((modules / ".modules.yaml").exists())
                self.assertFalse((modules / ".pnpm-workspace-state-v1.json").exists())
                self.assertEqual((modules / "dependency" / "index.js").read_text(), "module.exports = 1")

    def test_public_ci_rebuilds_actual_runtime_before_cache_publication(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        name = "Verify BB and Symphony runtime reproducibility before publishing"
        step = workflow_step(ci, name)
        self.assertIn("matrix.package == 'bb-ide' || matrix.package == 'symphony-ts'", step)
        self.assertIn("inputs.source_revision == ''", step)
        self.assertIn("nix build --rebuild --keep-failed --no-link", step)
        self.assertIn('diff -qr -- "$runtime" "$runtime.check" || true', step)
        self.assertIn("            exit 1", step)
        self.assertNotIn("continue-on-error", step)
        self.assertLess(ci.index(name), ci.index("      - name: Build package\n"))

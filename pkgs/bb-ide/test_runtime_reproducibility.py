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
                if package == "bb-ide":
                    config = dependency.parent / "build" / "config.gypi"
                    config.parent.mkdir()
                    config.write_text('{"store_dir": "/build/tmp.random"}')
                    addon = config.parent / "Release" / "addon.node"
                    addon.parent.mkdir()
                    addon.write_text("compiled runtime addon")
                    cleanup = next(line for line in recipe.splitlines()
                                   if "find " in line and "config.gypi" in line)
                    subprocess.run(["bash", "-eu", "-c", cleanup], env={**os.environ, "out": temp}, check=True)
                    self.assertFalse(config.exists())
                    self.assertEqual(addon.read_text(), "compiled runtime addon")

    def test_public_ci_rebuilds_actual_runtime_before_cache_publication(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        name = "Verify BB and Symphony runtime reproducibility before publishing"
        step = workflow_step(ci, name)
        self.assertIn("matrix.package == 'bb-ide' || matrix.package == 'symphony-ts'", step)
        self.assertIn("inputs.source_revision == ''", step)
        self.assertIn("nix build --rebuild --keep-failed --no-link", step)
        self.assertIn('diff -qr --no-dereference -- "$runtime" "$runtime.check" || true', step)
        self.assertIn("            exit 1", step)
        self.assertNotIn("continue-on-error", step)
        self.assertLess(ci.index(name), ci.index("      - name: Build package\n"))

    def test_diagnostics_compare_symlinks_without_following_dependency_cycles(self):
        with tempfile.TemporaryDirectory() as temp:
            roots = [Path(temp) / name for name in ("runtime", "runtime.check")]
            for index, root in enumerate(roots):
                root.mkdir()
                (root / "dependency-link").symlink_to(".")
                (root / "installer-state").write_text(f"volatile state {index}")
            result = subprocess.run(["diff", "-qr", "--no-dereference", "--", *map(str, roots)],
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("installer-state", result.stdout)
            self.assertNotIn("volatile state", result.stdout)
            self.assertNotIn("recursive", result.stderr)

from __future__ import annotations

from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "ensure-hermes-registration-lifecycle"


class HermesRegistrationLifecycleTests(unittest.TestCase):
    def test_stable_release_selection_rejects_rolling_and_downgrades(self):
        script = (ROOT / "scripts/update-upstream-inputs").read_text()
        selector = "update_hermes_release_ref() {" + script.split(
            "update_hermes_release_ref() {", 1
        )[1].split("\n}\n", 1)[0] + "\n}\n"
        for latest, accepted in (("v2026.9.24", True), ("v2026.10.1", True),
                                 ("v2026.9.14", False), ("main", False),
                                 ("v2026.10.1-rc1", False)):
            with self.subTest(latest=latest), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "flake.nix"
                before = '    hermes-agent.url = "github:NousResearch/hermes-agent/v2026.9.24";\n'
                path.write_text(before)
                result = subprocess.run(
                    ["bash", "-euc", selector
                     + f'latest_release_tag() {{ echo "{latest}"; }}\nupdate_hermes_release_ref'],
                    cwd=tmp, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                self.assertEqual(path.read_text(), before.replace("v2026.9.24", latest)
                                 if accepted else before)

    def _fixture(
        self, root: Path, *, declared: bool, installed: str | None
    ) -> tuple[Path, Path]:
        source = root / "source"
        site_packages = root / "site-packages"
        (source / "hermes_cli").mkdir(parents=True)
        site_packages.mkdir()
        (source / "hermes_cli" / "plugins.py").write_text(
            "from registration_lifecycle import replacement_coordinator\n"
        )
        (source / "registration_lifecycle.py").write_text("source implementation\n")
        modules = '  "registration_lifecycle",\n' if declared else ""
        (source / "pyproject.toml").write_text(
            f'[tool.setuptools]\npy-modules = [\n{modules}  "hermes_constants",\n]\n'
        )
        if installed is not None:
            (site_packages / "registration_lifecycle.py").write_text(installed)
        return source, site_packages

    def _run(self, source: Path, site_packages: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(HELPER), str(source), str(site_packages)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_old_source_is_copied_and_new_source_is_preserved(self) -> None:
        cases = (
            ("old-source", False, None, "source implementation\n"),
            ("new-source", True, "upstream packaged\n", "upstream packaged\n"),
            ("dynamic-modules", False, "upstream packaged\n", "upstream packaged\n"),
        )
        for name, declared, installed, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                source, site_packages = self._fixture(
                    Path(temporary), declared=declared, installed=installed
                )
                result = self._run(source, site_packages)
                self.assertEqual(result.returncode, 0, result.stderr)
                target = site_packages / "registration_lifecycle.py"
                self.assertEqual(target.read_text(), expected)
                if installed is None:
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)

    def test_declared_but_missing_module_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, site_packages = self._fixture(
                Path(temporary), declared=True, installed=None
            )
            result = self._run(source, site_packages)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("declares registration_lifecycle", result.stderr)

    def test_site_packages_comes_from_the_built_environment(self) -> None:
        assignment = next(
            line.strip()
            for line in (ROOT / "flake.nix").read_text().splitlines()
            if line.strip().startswith("sitePackages=")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = self._fixture(root, declared=False, installed=None)
            environment = root / "environment"
            subprocess.run(
                [sys.executable, "-m", "venv", "--without-pip", str(environment)],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [
                    "bash", "-euc",
                    assignment + '\nexec bash "$1" "$2" "$sitePackages"',
                    "hermes-env-test", str(HELPER), str(source),
                ],
                env={**os.environ, "out": str(environment)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = subprocess.run(
                [str(environment / "bin/python"), "-c",
                 "import importlib.util; print(importlib.util.find_spec('registration_lifecycle').origin)"],
                check=True,
                capture_output=True,
                text=True,
            )
            target = Path(installed.stdout.strip())
            self.assertTrue(target.is_relative_to(environment))
            self.assertEqual(target.read_text(), "source implementation\n")

    def test_missing_site_packages_fails_without_creating_stale_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = self._fixture(root, declared=False, installed=None)
            stale_directory = root / "environment/lib/python3.12/site-packages"
            result = self._run(source, stale_directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("site-packages directory is missing", result.stderr)
            self.assertFalse(stale_directory.exists())


if __name__ == "__main__":
    unittest.main()

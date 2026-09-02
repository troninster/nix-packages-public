from __future__ import annotations

from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "ensure-hermes-registration-lifecycle"


class HermesRegistrationLifecycleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

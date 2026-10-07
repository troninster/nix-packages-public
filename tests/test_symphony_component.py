import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SymphonyComponentTests(unittest.TestCase):
    def test_pilot_changed_files_select_only_symphony(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)

            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True,
                                      capture_output=True, text=True).stdout.strip()

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            for package in ("symphony-ts", "notion-cli"):
                recipe = repo / "pkgs" / package / "default.nix"
                recipe.parent.mkdir(parents=True)
                recipe.write_text("{}")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            base = git("rev-parse", "HEAD")
            for path in ("pkgs/symphony-ts/flake.nix", "pkgs/symphony-ts/flake.lock",
                         "scripts/build-symphony-component", "tests/test_symphony_component.py",
                         ".github/workflows/symphony-component.yml", "scripts/detect-ci-packages"):
                changed = repo / path
                changed.parent.mkdir(parents=True, exist_ok=True)
                changed.write_text("component fixture")
            git("add", ".")
            git("commit", "-q", "-m", "pilot")
            result = subprocess.run([str(ROOT / "scripts/detect-ci-packages"), base, "HEAD"],
                                    cwd=repo, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            self.assertEqual(json.loads(outputs["packages_json"]), ["symphony-ts"])
            self.assertEqual(outputs["run_checks"], "true")

    def test_component_lock_has_only_its_owned_nixpkgs(self):
        lock = json.loads((ROOT / "pkgs/symphony-ts/flake.lock").read_text())
        self.assertEqual(set(lock["nodes"]), {"root", "nixpkgs"})
        self.assertEqual(lock["nodes"]["root"]["inputs"], {"nixpkgs": "nixpkgs"})
        self.assertNotIn("inputs", lock["nodes"]["nixpkgs"])

    def test_build_requires_repeat_success_before_runtime_smoke(self):
        for rebuild_status in (0, 1):
            with self.subTest(rebuild_status=rebuild_status), tempfile.TemporaryDirectory() as temp:
                fixture = Path(temp)
                commands = fixture / "commands"
                runtime = fixture / "runtime"
                (runtime / "bin").mkdir(parents=True)
                cli = runtime / "bin/symphony"
                cli.write_text('#!/usr/bin/env bash\nprintf "smoke %s\\n" "$*" >> "$COMMAND_LOG"\nprintf "symphony usage\\n"\n')
                cli.chmod(0o755)
                nix = fixture / "nix"
                nix.write_text('''#!/usr/bin/env bash
printf 'nix %s\n' "$*" >> "$COMMAND_LOG"
if [[ "$*" == *--rebuild* ]]; then
  exit "$REBUILD_STATUS"
fi
if [[ "$1" == build ]]; then
  printf '%s\n' "$RUNTIME"
fi
''')
                nix.chmod(0o755)
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts/build-symphony-component")],
                    cwd=ROOT,
                    env={**os.environ, "PATH": f"{fixture}:{os.environ['PATH']}",
                         "COMMAND_LOG": str(commands), "RUNTIME": str(runtime),
                         "REBUILD_STATUS": str(rebuild_status), "REQUIRE_CACHIX_PUSH": "1"},
                    capture_output=True, text=True,
                )
                recorded = commands.read_text().splitlines()
                self.assertEqual(result.returncode, rebuild_status, result.stderr)
                self.assertTrue(recorded[0].startswith("nix flake check ./pkgs/symphony-ts "))
                for command in recorded[:3]:
                    self.assertIn("--no-update-lock-file", command)
                    self.assertIn("--no-write-lock-file", command)
                self.assertIn("--print-out-paths ./pkgs/symphony-ts#symphony-ts", recorded[1])
                self.assertIn("--rebuild --keep-failed --no-link ./pkgs/symphony-ts#symphony-ts", recorded[2])
                if rebuild_status:
                    self.assertEqual(len(recorded), 3)
                    self.assertEqual(result.stdout, "")
                else:
                    self.assertEqual(recorded[3], "smoke --help")
                    self.assertEqual(result.stdout.strip(), str(runtime))


if __name__ == "__main__":
    unittest.main()

import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "update-upstreams.yml"
UPDATER = ROOT / "scripts" / "update-upstream-inputs"
ARTIFACT_TOOL = ROOT / "scripts" / "update-artifact"
PACKAGE_DETECTOR = ROOT / "scripts" / "detect-ci-packages"
CODEX_HASH_UPDATER = ROOT / "scripts" / "update-codex-cargo-hashes"
CAMOFOX_LOCK_REPAIR = ROOT / "scripts" / "repair-camofox-package-lock.py"


def workflow_step(source: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = source.index(marker)
    end = source.find("\n      - name: ", start + len(marker))
    return source[start:] if end == -1 else source[start:end]


def workflow_job(source: str, name: str) -> str:
    marker = f"  {name}:\n"
    start = source.index(marker)
    next_job = re.search(r"^  [a-z0-9-]+:\n", source[start + len(marker) :], re.MULTILINE)
    if next_job is None:
        return source[start:]
    end = start + len(marker) + next_job.start()
    return source[start:end]


class UpdateUpstreamsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text()
        cls.updater = UPDATER.read_text()
        cls.detector = PACKAGE_DETECTOR.read_text()

    def test_verified_codex_commit_precedes_unrelated_updates(self) -> None:
        codex_update = workflow_step(self.workflow, "Update Codex input")
        codex_build = workflow_step(self.workflow, "Build verified Codex update")
        codex_commit = workflow_step(
            self.workflow, "Commit and push verified Codex update"
        )
        remaining_update = workflow_step(
            self.workflow, "Update remaining upstream inputs"
        )
        remaining_build = workflow_step(self.workflow, "Build changed packages")

        self.assertIn("./scripts/update-upstream-inputs --codex-only", codex_update)
        self.assertIn("./scripts/build-package codex", codex_build)
        self.assertIn("git push", codex_commit)
        self.assertIn(
            "./scripts/update-upstream-inputs --without-codex", remaining_update
        )
        self.assertLess(self.workflow.index(codex_build), self.workflow.index(codex_commit))
        self.assertLess(
            self.workflow.index(codex_commit), self.workflow.index(remaining_update)
        )
        self.assertLess(
            self.workflow.index(remaining_update), self.workflow.index(remaining_build)
        )
        remaining_job = workflow_job(self.workflow, "prepare-remaining")
        self.assertIn("- publish-codex", remaining_job)

    def test_prepare_jobs_are_read_only_and_publish_jobs_are_narrow(self) -> None:
        prepare_codex = workflow_job(self.workflow, "prepare-codex")
        publish_codex = workflow_job(self.workflow, "publish-codex")
        prepare_remaining = workflow_job(self.workflow, "prepare-remaining")
        publish_remaining = workflow_job(self.workflow, "publish-remaining")

        for job in (prepare_codex, prepare_remaining):
            self.assertIn("permissions:\n      contents: read", job)
            self.assertIn("persist-credentials: false", job)
            self.assertNotIn("git push", job)
        for job in (publish_codex, publish_remaining):
            self.assertIn("permissions:\n      contents: write", job)
            self.assertIn("update-artifact verify-apply", job)
            self.assertIn("git push origin HEAD:refs/heads/main", job)
            self.assertNotIn("build-package", job)
            self.assertNotIn("CACHIX_AUTH_TOKEN", job)

    def test_secrets_are_scoped_to_the_steps_that_need_them(self) -> None:
        self.assertEqual(self.workflow.count("secrets.CACHIX_AUTH_TOKEN"), 6)
        for step_name in ("Detect Cachix configuration", "Configure Cachix"):
            for step in re.findall(
                rf"      - name: {step_name}\n.*?(?=\n      - name: |\n  [a-z0-9-]+:|\Z)",
                self.workflow,
                re.DOTALL,
            ):
                self.assertIn("secrets.CACHIX_AUTH_TOKEN", step)
        for step_name in ("Build verified Codex update", "Build changed packages"):
            step = workflow_step(self.workflow, step_name)
            self.assertIn("secrets.CACHIX_AUTH_TOKEN", step)
            self.assertIn("REQUIRE_CACHIX_PUSH: 1", step)

    def test_codex_prepare_failure_does_not_starve_remaining_updates(self) -> None:
        remaining_job = workflow_job(self.workflow, "prepare-remaining")
        self.assertIn("needs.prepare-codex.result == 'failure'", remaining_job)
        self.assertIn("needs.prepare-codex.result == 'success'", remaining_job)
        self.assertIn("needs.publish-codex.result == 'success'", remaining_job)
        self.assertIn("needs.publish-codex.result == 'skipped'", remaining_job)
        self.assertNotIn("needs.publish-codex.result == 'failure'", remaining_job)

    def test_every_action_is_pinned_to_a_full_sha_with_version_comment(self) -> None:
        uses_lines = re.findall(
            r"^\s*uses:\s+([^@\s]+)@([^\s]+)(.*)$", self.workflow, re.MULTILINE
        )
        self.assertGreater(len(uses_lines), 0)
        for action, ref, suffix in uses_lines:
            self.assertRegex(ref, r"^[0-9a-f]{40}$", action)
            self.assertRegex(suffix, r"\s+# v\d+(?:\.\d+){0,2}$", action)

        expected = {
            "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
            "easimon/maximize-build-space": "fc881a613ad2a34aca9c9624518214ebc21dfc0c",
            "cachix/install-nix-action": "630ae543ea3a38a9a4166f03376c02c50f408342",
            "cachix/cachix-action": "5f2d7c5294214f71b873db4b969586b980625e71",
            "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
        }
        for action, sha in expected.items():
            self.assertIn(f"uses: {action}@{sha}", self.workflow)

    def test_publish_jobs_revalidate_artifact_and_base_before_push(self) -> None:
        for job_name in ("publish-codex", "publish-remaining"):
            job = workflow_job(self.workflow, job_name)
            self.assertIn("git ls-remote origin refs/heads/main", job)
            self.assertGreaterEqual(job.count('"$remote_sha" != "$EXPECTED_BASE_SHA"'), 2)
            self.assertIn("update-artifact verify-apply", job)
        publish_codex = workflow_job(self.workflow, "publish-codex")
        self.assertIn("Reverify Codex release identity", publish_codex)
        self.assertIn('if [[ "$tag_commit" != "$lock_rev" ]]', publish_codex)

    def test_selective_updater_modes_keep_codex_out_of_remaining_phase(self) -> None:
        self.assertIn("--codex-only", self.updater)
        self.assertIn("--without-codex", self.updater)
        selection = self.updater.index(
            'case "$update_mode" in', self.updater.index("block_symphony_ts()")
        )
        codex_mode = self.updater.index("  codex-only)\n", selection)
        without_mode = self.updater.index("  without-codex)\n", codex_mode)
        codex_block = self.updater[codex_mode:without_mode]
        self.assertIn("run_block codex_ref", codex_block)
        self.assertIn("run_block codex_cargo_hashes", codex_block)
        self.assertIn(
            "for required_block in codex_ref flake_update codex_cargo_hashes",
            codex_block,
        )
        self.assertNotIn("block_camofox", codex_block)

        remaining_mode = self.updater.index("  without-codex)\n", without_mode)
        remaining_end = self.updater.index("esac", remaining_mode)
        remaining_block = self.updater[remaining_mode:remaining_end]
        self.assertIn("block_camofox", remaining_block)
        self.assertNotIn("run_block codex_ref", remaining_block)

    def test_remaining_lane_covers_external_packages_only(self) -> None:
        expected_blocks = (
            "devspace",
            "freellmapi",
            "github_cli",
            "notion_cli",
            "omp",
            "supabase_cli",
        )
        for block in expected_blocks:
            with self.subTest(block=block):
                self.assertIn(f"block_{block}()", self.updater)
                self.assertIn(f"run_block {block}", self.updater)

        for excluded in ("render-cli", "vexora", "camoufox-agent"):
            with self.subTest(excluded=excluded):
                self.assertNotIn(excluded, self.updater)
        self.assertNotIn('add_package "camoufox-agent"', self.detector.split(
            ".github/workflows/update-upstreams.yml | scripts/update-upstream-inputs)"
        )[1].split("scripts/update-codex-cargo-hashes)")[0])

    def test_update_blocks_declare_their_transaction_files(self) -> None:
        expected = {
            "camofox": ("block_camofox", "$camofox_package_file"),
            "symphony_ts": ("block_symphony_ts", "$symphony_ts_package_file"),
            "devspace": ("block_devspace", "$devspace_package_file"),
            "freellmapi": ("block_freellmapi", "$freellmapi_package_file"),
            "github_cli": ("block_github_cli", "$github_cli_package_file"),
            "notion_cli": ("block_notion_cli", "$notion_cli_package_file"),
            "omp": ("block_omp", "$omp_package_file"),
            "supabase_cli": ("block_supabase_cli", "$supabase_cli_package_file"),
            "flake_update": ("block_flake_update", "flake.lock"),
        }
        for name, (function, path) in expected.items():
            with self.subTest(name=name):
                self.assertRegex(
                    self.updater,
                    rf"run_block\s+{name}\s+{function}\s+\"?{re.escape(path)}\"?",
                )

    def test_failed_block_transactions_restore_files_and_no_changed_package(self) -> None:
        start = self.updater.index("run_block() {")
        end = self.updater.index("\n}\n\nlock_fingerprint", start) + 2
        run_block = self.updater[start:end]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / ".changed-packages"
            script = f"""
set -euo pipefail
declare -A block_status=()
{run_block}
fail_block() {{ printf 'mutated\\n' > "$FAIL_TARGET"; return 1; }}
for name in camofox omp flake_update; do
  target={shlex.quote(str(root))}/$name
  printf 'before-%s\\n' "$name" > "$target"
  FAIL_TARGET="$target" run_block "$name" fail_block "$target" >/dev/null 2>&1
  test "$(cat "$target")" = "before-$name"
done
test ! -e {shlex.quote(str(changed))}
"""
            result = subprocess.run(
                ["bash", "-c", script],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_updater_script_changes_select_every_managed_package(self) -> None:
        managed = (
            "archon",
            "camofox-browser",
            "codex",
            "devspace",
            "freellmapi",
            "github-cli",
            "hermes-agent",
            "notion-cli",
            "omp",
            "supabase-cli",
            "symphony-ts",
        )
        for package in managed:
            with self.subTest(package=package):
                self.assertIn(f'add_package "{package}"', self.detector)

    def test_codex_and_hermes_current_upgrade_contracts(self) -> None:
        flake = (ROOT / "flake.nix").read_text()
        self.assertIn('github:openai/codex/rust-v0.149.1', flake)
        self.assertNotIn("registration_lifecycle", flake)
        self.assertIn('#![recursion_limit = "256"]', flake)
        lock = json.loads((ROOT / "flake.lock").read_text())
        self.assertEqual(lock["nodes"]["codex"]["original"]["ref"], "rust-v0.149.1")
        self.assertEqual(lock["nodes"]["hermes-agent"]["locked"]["rev"], "057dcdf236f8a6a26721c10fcc6ccb72726e272a")

    def test_current_codex_source_and_lock_identities_match(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        ref_marker = 'url = "github:openai/codex/'
        ref_start = flake_source.index(ref_marker) + len(ref_marker)
        source_ref = flake_source[ref_start : flake_source.index('";', ref_start)]

        lock = json.loads((ROOT / "flake.lock").read_text())
        codex = lock["nodes"]["codex"]
        self.assertRegex(source_ref, r"^rust-v\d+\.\d+\.\d+$")
        self.assertEqual(codex["original"]["ref"], source_ref)
        self.assertRegex(codex["locked"]["rev"], r"^[0-9a-f]{40}$")

    def test_codex_v8_sandbox_pair_version_is_derived_from_cargo_lock(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        self.assertIn(
            'codexCargoLock = builtins.fromTOML (builtins.readFile "${codex}/codex-rs/Cargo.lock")',
            flake_source,
        )
        self.assertIn("builtins.length codexV8Versions == 1", flake_source)
        env_start = flake_source.index("    codexBuildEnv = pkgs:")
        env_end = flake_source.index("    codexBuildFlags = [", env_start)
        build_env = flake_source[env_start:env_end]
        self.assertEqual(build_env.count("RUSTY_V8_ARCHIVE ="), 1)
        self.assertEqual(build_env.count("RUSTY_V8_SRC_BINDING_PATH ="), 1)
        self.assertIn(
            'url = "https://github.com/openai/codex/releases/download/'
            'rusty-v8-v${codexV8Version}/'
            'librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz";',
            build_env,
        )
        self.assertIn(
            'url = "https://github.com/openai/codex/releases/download/'
            'rusty-v8-v${codexV8Version}/'
            'src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs";',
            build_env,
        )
        self.assertEqual(
            len(re.findall(r'hash = "sha256-[A-Za-z0-9+/]{43}=";', build_env)),
            2,
            "the sandbox archive and binding must each have a valid SRI hash",
        )

    def test_paperclip_stays_out_of_active_update_surfaces(self) -> None:
        self.assertNotIn("paperclip", self.workflow.lower())
        self.assertNotIn("paperclip", self.updater.lower())

    def test_unknown_selector_fails_before_updating(self) -> None:
        result = subprocess.run(
            [str(UPDATER), "--unknown-mode"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


class CodexPackageContractTests(unittest.TestCase):
    def test_codex_post_patch_inserts_exec_recursion_limit_idempotently(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        patch_start = flake_source.index("codexRecursionLimitPatch = ''")
        patch_end = flake_source.index("'';", patch_start)
        post_patch = flake_source[patch_start:patch_end]

        self.assertIn(
            "if ! grep -Fqx '#![recursion_limit = \"256\"]' \"$target\"; then",
            post_patch,
        )
        self.assertIn(
            "sed -i '1i#![recursion_limit = \"256\"]' \"$target\"",
            post_patch,
        )
        self.assertIn(
            "grep -Fqx '#![recursion_limit = \"256\"]' \"$target\"",
            post_patch,
        )

    def test_codex_post_patch_targets_exec_and_cli_with_shared_helper(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        patch_start = flake_source.index("codexRecursionLimitPatch = ''")
        patch_end = flake_source.index("'';", patch_start)
        helper = flake_source[patch_start:patch_end]
        self.assertIn("add_recursion_limit()", helper)
        self.assertIn("add_recursion_limit exec/src/lib.rs", helper)
        self.assertIn("add_recursion_limit cli/src/main.rs", helper)
        self.assertEqual(
            helper.count("grep -Fqx '#![recursion_limit = \"256\"]' \"$target\""),
            2,
        )

    def test_codex_builds_and_requires_runtime_executables(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        flags_start = flake_source.index("    codexBuildFlags = [")
        flags_end = flake_source.index("    ];", flags_start)
        build_flags = re.findall(r'"([^"]+)"', flake_source[flags_start:flags_end])

        self.assertEqual(
            build_flags,
            [
                "--package",
                "codex-cli",
                "--bin",
                "codex",
                "--package",
                "codex-code-mode-host",
                "--bin",
                "codex-code-mode-host",
            ],
        )
        for executable in ("codex", "codex-code-mode-host"):
            with self.subTest(executable=executable):
                self.assertIn(
                    f'test -x "$out/bin/{executable}"',
                    flake_source,
                    f"Codex output must fail closed when {executable} is absent",
                )

    def test_codex_post_install_safely_composes_inherited_hooks(self) -> None:
        flake_path = json.dumps(str(ROOT / "flake.nix"))
        root_ref = json.dumps(f"path:{ROOT}")
        expression = f"""
          let
            flake = import (builtins.fromJSON {json.dumps(flake_path)});
            resolved = builtins.getFlake (builtins.fromJSON {json.dumps(root_ref)});
            system = "x86_64-linux";
            upstreamPackage = resolved.inputs.codex.packages.${{system}}.default;
            compose = inheritedHook:
              let
                syntheticCodex = resolved.inputs.codex // {{
                  packages = resolved.inputs.codex.packages // {{
                    ${{system}} = resolved.inputs.codex.packages.${{system}} // {{
                      default = upstreamPackage.overrideAttrs (_: {{
                        postInstall = inheritedHook;
                      }});
                    }};
                  }};
                }};
              in
              (flake.outputs {{
                self = {{}};
                inherit (resolved.inputs) nixpkgs hermes-agent rust-overlay;
                codex = syntheticCodex;
              }}).packages.${{system}}.codex.postInstall;
          in
          {{
            empty = compose "";
            trailingNewline = compose "old-command\\n";
            noTrailingNewline = compose "old-command";
          }}
        """
        result = subprocess.run(
            ["nix", "eval", "--json", "--impure", "--expr", expression],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        hooks = json.loads(result.stdout)
        appended_hook = (
            'test -x "$out/bin/codex"\n'
            'test -x "$out/bin/codex-code-mode-host"\n'
        )

        self.assertEqual(hooks["empty"], appended_hook)
        self.assertEqual(hooks["trailingNewline"], "old-command\n" + appended_hook)
        self.assertEqual(hooks["noTrailingNewline"], "old-command\n" + appended_hook)


class ExternalPackageContractTests(unittest.TestCase):
    def test_camofox_repair_runs_before_npm_prefetch(self) -> None:
        source = (ROOT / "scripts" / "update-upstream-inputs").read_text()
        repair = source.index("repair-camofox-package-lock.py")
        prefetch = source.index("prefetch-npm-deps", repair)
        self.assertLess(repair, prefetch)
        package = (ROOT / "pkgs" / "camofox-browser" / "default.nix").read_text()
        self.assertIn("repair-camofox-package-lock.py", package)

    def test_camofox_lock_repair_handles_114_fixture_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "package-lock.json"
            package = root / "package.json"
            top_level = {
                "version": "13.0.6",
                "resolved": "https://registry.npmjs.org/glob/-/glob-13.0.6.tgz",
                "integrity": "sha512-top-level",
            }
            package.write_text(json.dumps({
                "name": "@askjo/camofox-browser",
                "version": "1.14.0",
                "overrides": {
                    "@jest/reporters": {"glob": "13.0.6"},
                    "jest-config": {"glob": "13.0.6"},
                    "jest-runtime": {"glob": "13.0.6"},
                    "swagger-jsdoc": {"glob": "13.0.6"},
                },
            }))
            stale = {"version": "11.1.0", "resolved": "old", "integrity": "old"}
            jest_stale = {**stale, "dev": True}
            lock.write_text(json.dumps({
                "name": "@askjo/camofox-browser",
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/glob": top_level,
                    "node_modules/@jest/reporters/node_modules/glob": jest_stale,
                    "node_modules/jest-config/node_modules/glob": jest_stale,
                    "node_modules/jest-runtime/node_modules/glob": jest_stale,
                    "node_modules/swagger-jsdoc/node_modules/glob": stale,
                },
            }))
            first = subprocess.run(
                ["python3", str(CAMOFOX_LOCK_REPAIR), str(lock), str(package)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            repaired = lock.read_bytes()
            data = json.loads(repaired)
            for path in (
                "node_modules/@jest/reporters/node_modules/glob",
                "node_modules/jest-config/node_modules/glob",
                "node_modules/jest-runtime/node_modules/glob",
                "node_modules/swagger-jsdoc/node_modules/glob",
            ):
                self.assertEqual(data["packages"][path]["version"], top_level["version"])
            for path in (
                "node_modules/@jest/reporters/node_modules/glob",
                "node_modules/jest-config/node_modules/glob",
                "node_modules/jest-runtime/node_modules/glob",
            ):
                self.assertTrue(data["packages"][path]["dev"])
            self.assertNotIn(
                "dev", data["packages"]["node_modules/swagger-jsdoc/node_modules/glob"]
            )
            second = subprocess.run(
                ["python3", str(CAMOFOX_LOCK_REPAIR), str(lock), str(package)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(lock.read_bytes(), repaired)

    def test_camofox_lock_repair_rejects_unexpected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package.json"
            lock = root / "package-lock.json"
            package.write_text(json.dumps({
                "overrides": {
                    "@jest/reporters": {"glob": "13.0.6"},
                    "jest-config": {"glob": "13.0.6"},
                    "jest-runtime": {"glob": "13.0.6"},
                    "swagger-jsdoc": {"glob": "13.0.6"},
                },
            }))
            lock.write_text(json.dumps({
                "lockfileVersion": 3,
                "packages": {
                    "node_modules/glob": {"version": "13.0.6"},
                    "node_modules/unexpected/node_modules/glob": {
                        "version": "11.1.0",
                    },
                },
            }))
            result = subprocess.run(
                ["python3", str(CAMOFOX_LOCK_REPAIR), str(lock), str(package)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected Camofox glob paths", result.stderr)

    def test_go_packages_use_pinned_go126_builder(self) -> None:
        flake = (ROOT / "flake.nix").read_text()
        self.assertIn(
            "codex.inputs.nixpkgs.legacyPackages.${system}.buildGo126Module",
            flake,
        )
        for package in ("github-cli", "supabase-cli"):
            with self.subTest(package=package):
                source = (ROOT / "pkgs" / package / "default.nix").read_text()
                self.assertIn("buildGo126Module", source)
                self.assertNotIn("buildGo125Module", source)

    def test_freellmapi_keeps_server_client_scope(self) -> None:
        source = (ROOT / "pkgs" / "freellmapi" / "default.nix").read_text()
        self.assertIn("npm run build:server", source)
        self.assertIn("npm run build -w client", source)
        self.assertNotIn("\nnpm run build -w cli\n", source)
        self.assertIn("server/dist server/package.json", source)
        self.assertIn("client/dist client/package.json", source)
        self.assertIn(
            "rm -f $out/lib/freellmapi/node_modules/freellmapi", source
        )
        self.assertIn(
            "test ! -e $out/lib/freellmapi/node_modules/freellmapi", source
        )

    def test_supabase_uses_nested_go_module(self) -> None:
        source = (ROOT / "pkgs" / "supabase-cli" / "default.nix").read_text()
        self.assertIn('sourceRoot = "source/apps/cli-go";', source)
        self.assertIn('subPackages = [ "." ];', source)
        self.assertIn("github.com/supabase/cli/internal/utils.Version", source)

    def test_automated_package_paths_are_allowed_in_remaining_artifacts(self) -> None:
        artifact = (ROOT / "scripts" / "update-artifact").read_text()
        for package in (
            "devspace",
            "freellmapi",
            "github-cli",
            "notion-cli",
            "omp",
            "supabase-cli",
        ):
            with self.subTest(package=package):
                self.assertIn(f'"{package}"', artifact)
                self.assertIn(f'"pkgs/{package}/default.nix"', artifact)
        for excluded in ("render-cli", "vexora", "camoufox-agent"):
            self.assertNotIn(excluded, artifact)


class CodexCargoHashUpdaterTests(unittest.TestCase):
    OLD_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    NEW_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
    V8_ARCHIVE_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
    V8_BINDING_HASH = "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD="
    V8_VERSION = "150.4.0"
    RUNFILES_REV = "b56cbaa8465e74127f1ea216f813cd377295ad81"
    CROSSTERM_REV = "f69a4a0499f2fdc7d5d222df32373ffffe9ba3f5"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.flake = self.root / "flake.nix"
        self.lock = self.root / "Cargo.lock"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.prefetch = self.bin / "nix-prefetch-git"
        self.prefetch.write_text(
            f"""#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("FAIL_PREFETCH") == "1":
    raise SystemExit(1)
if "--fetch-submodules" not in sys.argv:
    raise SystemExit(3)
rev = sys.argv[sys.argv.index("--rev") + 1]
url = sys.argv[sys.argv.index("--url") + 1]
hashes = {{
    "{self.RUNFILES_REV}": "{self.OLD_HASH}",
    "{self.CROSSTERM_REV}": "{self.NEW_HASH}",
}}
urls = {{
    "{self.RUNFILES_REV}": "https://github.com/dzbarsky/rules_rust",
    "{self.CROSSTERM_REV}": "https://github.com/openai-oss-forks/crossterm",
}}
if url != urls[rev]:
    raise SystemExit(2)
print(json.dumps({{"rev": rev, "hash": hashes[rev]}}))
"""
        )
        self.prefetch.chmod(0o755)
        self.nix = self.bin / "nix"
        self.nix.write_text(
            f"""#!/usr/bin/env python3
import json
import os
import sys

base = "https://github.com/openai/codex/releases/download/rusty-v8-v{self.V8_VERSION}"
archive = base + "/librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz"
binding = base + "/src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs"
if sys.argv[1:4] != ["store", "prefetch-file", "--json"]:
    raise SystemExit(2)
url = sys.argv[4]
if url == archive:
    if os.environ.get("FAIL_V8_ARCHIVE_PREFETCH") == "1":
        raise SystemExit(1)
    output_hash = "{self.V8_ARCHIVE_HASH}"
elif url == binding:
    if os.environ.get("FAIL_V8_BINDING_PREFETCH") == "1":
        raise SystemExit(1)
    output_hash = "{self.V8_BINDING_HASH}"
else:
    raise SystemExit(2)
print(json.dumps({{"hash": output_hash, "storePath": "/nix/store/test-v8"}}))
"""
        )
        self.nix.chmod(0o755)
        self.flake.write_text(
            f"""{{
  outputs = inputs:
  let
    codexCargoOutputHashes = lib: {{
      "crossterm-0.28.1" = "{self.OLD_HASH}";
      "ratatui-0.29.0" = "{self.OLD_HASH}";
      "runfiles-0.1.0" = "{self.OLD_HASH}";
    }};
    codexV8Version = "146.4.0";
    codexBuildEnv = pkgs: {{
      RUSTY_V8_ARCHIVE = pkgs.fetchurl {{
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${{codexV8Version}}/librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz";
        hash = "{self.OLD_HASH}";
      }};
      RUSTY_V8_SRC_BINDING_PATH = pkgs.fetchurl {{
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${{codexV8Version}}/src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs";
        hash = "{self.OLD_HASH}";
      }};
    }};
  in {{ }};
}}
"""
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_updater(
        self,
        *,
        fail_prefetch: bool = False,
        fail_v8_archive_prefetch: bool = False,
        fail_v8_binding_prefetch: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        if fail_prefetch:
            env["FAIL_PREFETCH"] = "1"
        if fail_v8_archive_prefetch:
            env["FAIL_V8_ARCHIVE_PREFETCH"] = "1"
        if fail_v8_binding_prefetch:
            env["FAIL_V8_BINDING_PREFETCH"] = "1"
        return subprocess.run(
            [
                str(CODEX_HASH_UPDATER),
                "--lock-file",
                str(self.lock),
                "--flake-file",
                str(self.flake),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_0146_to_0147_replaces_stale_git_dependencies(self) -> None:
        self.lock.write_text(
            f"""version = 4

[[package]]
name = "runfiles"
version = "0.1.0"
source = "git+https://github.com/dzbarsky/rules_rust?rev={self.RUNFILES_REV}#{self.RUNFILES_REV}"

[[package]]
name = "crossterm"
version = "0.29.0"
source = "git+https://github.com/openai-oss-forks/crossterm?rev={self.CROSSTERM_REV}#{self.CROSSTERM_REV}"

[[package]]
name = "v8"
version = "{self.V8_VERSION}"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        )

        result = self.run_updater()

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = self.flake.read_text()
        self.assertNotIn("crossterm-0.28.1", updated)
        self.assertNotIn("ratatui-0.29.0", updated)
        self.assertIn(f'"crossterm-0.29.0" = "{self.NEW_HASH}";', updated)
        self.assertIn(f'"runfiles-0.1.0" = "{self.OLD_HASH}";', updated)
        self.assertIn(f'hash = "{self.V8_ARCHIVE_HASH}";', updated)
        self.assertIn(f'hash = "{self.V8_BINDING_HASH}";', updated)
        self.assertEqual(
            updated,
            f"""{{
  outputs = inputs:
  let
    codexCargoOutputHashes = lib: {{
      "crossterm-0.29.0" = "{self.NEW_HASH}";
      "runfiles-0.1.0" = "{self.OLD_HASH}";
    }};
    codexV8Version = "146.4.0";
    codexBuildEnv = pkgs: {{
      RUSTY_V8_ARCHIVE = pkgs.fetchurl {{
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${{codexV8Version}}/librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz";
        hash = "{self.V8_ARCHIVE_HASH}";
      }};
      RUSTY_V8_SRC_BINDING_PATH = pkgs.fetchurl {{
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${{codexV8Version}}/src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs";
        hash = "{self.V8_BINDING_HASH}";
      }};
    }};
  in {{ }};
}}
""",
        )

    def test_unknown_git_source_fails_without_editing(self) -> None:
        self.lock.write_text(
            f"""version = 4

[[package]]
name = "unknown"
version = "1.0.0"
source = "git+file:///tmp/unknown#{self.RUNFILES_REV}"
"""
        )
        before = self.flake.read_text()

        result = self.run_updater()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a public HTTPS URL", result.stderr)
        self.assertEqual(self.flake.read_text(), before)

    def test_prefetch_failure_fails_without_editing(self) -> None:
        self.lock.write_text(
            f"""version = 4

[[package]]
name = "runfiles"
version = "0.1.0"
source = "git+https://github.com/dzbarsky/rules_rust?rev={self.RUNFILES_REV}#{self.RUNFILES_REV}"

[[package]]
name = "v8"
version = "{self.V8_VERSION}"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        )
        before = self.flake.read_text()

        result = self.run_updater(fail_prefetch=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to prefetch", result.stderr)
        self.assertEqual(self.flake.read_text(), before)

    def test_v8_archive_prefetch_failure_fails_without_editing(self) -> None:
        self.lock.write_text(
            f"""version = 4

[[package]]
name = "runfiles"
version = "0.1.0"
source = "git+https://github.com/dzbarsky/rules_rust?rev={self.RUNFILES_REV}#{self.RUNFILES_REV}"

[[package]]
name = "v8"
version = "{self.V8_VERSION}"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        )
        before = self.flake.read_text()

        result = self.run_updater(fail_v8_archive_prefetch=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to prefetch rusty_v8 archive", result.stderr)
        self.assertEqual(self.flake.read_text(), before)

    def test_v8_binding_prefetch_failure_fails_without_editing(self) -> None:
        self.lock.write_text(
            f"""version = 4

[[package]]
name = "runfiles"
version = "0.1.0"
source = "git+https://github.com/dzbarsky/rules_rust?rev={self.RUNFILES_REV}#{self.RUNFILES_REV}"

[[package]]
name = "v8"
version = "{self.V8_VERSION}"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        )
        before = self.flake.read_text()

        result = self.run_updater(fail_v8_binding_prefetch=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to prefetch rusty_v8 binding", result.stderr)
        self.assertEqual(self.flake.read_text(), before)

    def test_v8_pair_block_validation_fails_without_editing(self) -> None:
        self.lock.write_text(
            f"""version = 4

[[package]]
name = "runfiles"
version = "0.1.0"
source = "git+https://github.com/dzbarsky/rules_rust?rev={self.RUNFILES_REV}#{self.RUNFILES_REV}"

[[package]]
name = "v8"
version = "{self.V8_VERSION}"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
        )
        original = self.flake.read_text()
        archive_block = f"""      RUSTY_V8_ARCHIVE = pkgs.fetchurl {{
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${{codexV8Version}}/librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz";
        hash = "{self.OLD_HASH}";
      }};
"""
        binding_block = f"""      RUSTY_V8_SRC_BINDING_PATH = pkgs.fetchurl {{
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${{codexV8Version}}/src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs";
        hash = "{self.OLD_HASH}";
      }};
"""
        cases = {
            "missing archive": (original.replace(archive_block, ""), "archive"),
            "duplicate archive": (
                original.replace(archive_block, archive_block * 2),
                "archive",
            ),
            "statically pinned archive URL": (
                original.replace(
                    "rusty-v8-v${codexV8Version}/librusty_v8_",
                    f"rusty-v8-v{self.V8_VERSION}/librusty_v8_",
                ),
                "archive",
            ),
            "missing binding": (original.replace(binding_block, ""), "binding"),
            "duplicate binding": (
                original.replace(binding_block, binding_block * 2),
                "binding",
            ),
            "statically pinned binding URL": (
                original.replace(
                    "rusty-v8-v${codexV8Version}/src_binding_",
                    f"rusty-v8-v{self.V8_VERSION}/src_binding_",
                ),
                "binding",
            ),
        }

        for name, (source, artifact) in cases.items():
            with self.subTest(name=name):
                self.flake.write_text(source)
                before = self.flake.read_text()

                result = self.run_updater()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"exactly one derived rusty_v8 {artifact} block", result.stderr
                )
                self.assertEqual(self.flake.read_text(), before)


class PackageDetectorTests(unittest.TestCase):
    def test_codex_hash_updater_change_selects_only_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            updater = repo / "scripts" / "update-codex-cargo-hashes"
            updater.parent.mkdir(parents=True)
            updater.write_text("#!/usr/bin/env bash\n# initial\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            updater.write_text("#!/usr/bin/env bash\n# changed\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "change updater"],
                cwd=repo,
                check=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            result = subprocess.run(
                [str(PACKAGE_DETECTOR), base, head],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('packages_json=["codex"]', result.stdout)
        self.assertIn("package_count=1", result.stdout)
        self.assertIn("run_checks=true", result.stdout)

    def test_deleted_package_is_not_selected_or_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            package_file = repo / "pkgs" / "removed" / "default.nix"
            package_file.parent.mkdir(parents=True)
            package_file.write_text("{}\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            package_file.unlink()
            subprocess.run(["git", "add", "-u"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "remove package"],
                cwd=repo,
                check=True,
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            result = subprocess.run(
                [str(PACKAGE_DETECTOR), base, head],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("packages_json=[]", result.stdout)
        self.assertIn("package_count=0", result.stdout)
        self.assertIn("run_checks=true", result.stdout)


class UpdateArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.artifact = self.root / "artifact"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.repo / "flake.nix").write_text("codex = old;\n")
        (self.repo / "flake.lock").write_text('{"version": 1}\n')
        (self.repo / "pkgs" / "archon").mkdir(parents=True)
        (self.repo / "pkgs" / "archon" / "default.nix").write_text("version = old;\n")
        self.git("add", "flake.nix", "flake.lock", "pkgs/archon/default.nix")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def tool(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ARTIFACT_TOOL), *args],
            cwd=self.repo,
            check=check,
            capture_output=True,
            text=True,
        )

    def create_codex_artifact(self) -> None:
        (self.repo / "flake.nix").write_text("codex = new;\n")
        (self.repo / "flake.lock").write_text('{"version": 2}\n')
        (self.repo / ".changed-packages").write_text("codex\n")
        self.tool(
            "create",
            "--phase",
            "codex",
            "--base-sha",
            self.base,
            "--packages-file",
            ".changed-packages",
            "--artifact-dir",
            str(self.artifact),
        )

    def reset_candidate(self) -> None:
        self.git("reset", "--hard", "-q", self.base)
        (self.repo / ".changed-packages").unlink(missing_ok=True)

    def test_round_trip_revalidates_and_applies_exact_codex_patch(self) -> None:
        self.create_codex_artifact()
        self.reset_candidate()
        self.tool(
            "verify-apply",
            "--phase",
            "codex",
            "--base-sha",
            self.base,
            "--artifact-dir",
            str(self.artifact),
        )
        self.assertEqual(
            self.git("diff", "--cached", "--name-only").stdout.splitlines(),
            ["flake.lock", "flake.nix"],
        )

    def test_round_trip_revalidates_remaining_package_metadata(self) -> None:
        package_file = self.repo / "pkgs" / "archon" / "default.nix"
        package_file.write_text("version = new;\n")
        (self.repo / ".changed-packages").write_text("archon\n")
        self.tool(
            "create",
            "--phase",
            "remaining",
            "--base-sha",
            self.base,
            "--packages-file",
            ".changed-packages",
            "--artifact-dir",
            str(self.artifact),
        )
        self.reset_candidate()
        self.tool(
            "verify-apply",
            "--phase",
            "remaining",
            "--base-sha",
            self.base,
            "--artifact-dir",
            str(self.artifact),
        )
        self.assertEqual(
            self.git("diff", "--cached", "--name-only").stdout.strip(),
            "pkgs/archon/default.nix",
        )

    def test_create_rejects_path_outside_phase_allowlist(self) -> None:
        (self.repo / "flake.lock").write_text('{"version": 2}\n')
        (self.repo / "unexpected.txt").write_text("no\n")
        (self.repo / ".changed-packages").write_text("codex\n")
        result = self.tool(
            "create",
            "--phase",
            "codex",
            "--base-sha",
            self.base,
            "--packages-file",
            ".changed-packages",
            "--artifact-dir",
            str(self.artifact),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected paths", result.stderr)

    def test_verify_rejects_tampered_patch(self) -> None:
        self.create_codex_artifact()
        self.reset_candidate()
        with (self.artifact / "update.patch").open("ab") as handle:
            handle.write(b"\n# tampered\n")
        result = self.tool(
            "verify-apply",
            "--phase",
            "codex",
            "--base-sha",
            self.base,
            "--artifact-dir",
            str(self.artifact),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr)

    def test_create_rejects_changed_package_metadata_that_disagrees_with_paths(self) -> None:
        (self.repo / "pkgs" / "archon" / "default.nix").write_text("version = new;\n")
        (self.repo / ".changed-packages").write_text("camofox-browser\n")
        result = self.tool(
            "create",
            "--phase",
            "remaining",
            "--base-sha",
            self.base,
            "--packages-file",
            ".changed-packages",
            "--artifact-dir",
            str(self.artifact),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match patch paths", result.stderr)


if __name__ == "__main__":
    unittest.main()

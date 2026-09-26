import json
import os
import re
import runpy
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "update-upstreams.yml"
UPDATER = ROOT / "scripts" / "update-upstream-inputs"
ARTIFACT_TOOL = ROOT / "scripts" / "update-artifact"
PACKAGE_DETECTOR = ROOT / "scripts" / "detect-ci-packages"
CODEX_HASH_UPDATER = ROOT / "scripts" / "update-codex-cargo-hashes"
CAMOFOX_LOCK_REPAIR = ROOT / "scripts" / "repair-camofox-package-lock.py"
CAMOFOX_COMPAT_PATCH = ROOT / "scripts" / "patch-camofox-browser.py"


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


class GitHubCliToolchainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = runpy.run_path(str(ROOT / "scripts/update-github-cli-toolchain"))

    def test_go_and_toolchain_requirements_are_respected(self):
        required = self.helper["required_version"]
        self.assertEqual(required("module gh\ngo 1.27 // minimum\ntoolchain go1.28.1\n"), (1, 28, 1))
        self.assertEqual(required("go 1.27.2\ntoolchain default\n"), (1, 27, 2))
        for source in ("module gh\n", "go 1.28rc1\n", "go 1.27\ngo 1.28\n"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                required(source)

    def test_selection_crosses_minor_versions_but_never_selects_prereleases(self):
        candidates = [
            {"attribute": "go_1_27", "version": "1.27.2"},
            {"attribute": "go_1_28", "version": "1.28.1"},
            {"attribute": "go_1_29", "version": "1.29rc2"},
        ]
        select = self.helper["select_toolchain"]
        self.assertEqual(select(candidates, (1, 27, 0)), ("go_1_28", "1.28.1"))
        self.assertEqual(select(candidates, (1, 28, 1)), ("go_1_28", "1.28.1"))
        with self.assertRaisesRegex(ValueError, "no stable Go"):
            select(candidates, (1, 29, 0))

    def test_discovery_pins_source_and_toolchain_without_downgrading(self):
        candidate_pin = self.helper["candidate_pin"]
        revision = "a" * 40
        source_hash = "sha256-" + "A" * 43 + "="
        answers = [
            revision + "\trefs/heads/nixos-unstable",
            json.dumps({"hash": source_hash, "storePath": "/nix/store/toolchain-source"}),
            json.dumps([{"attribute": "go_1_28", "version": "1.28.1"}]),
        ]
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "go.mod").write_text("go 1.27.0\ntoolchain go1.28.1\n")
            with mock.patch.dict(candidate_pin.__globals__, {"run": mock.Mock(side_effect=answers)}):
                pin = candidate_pin(source, {"go_version": "1.27.1", "nixpkgs_rev": "b" * 40})
            self.assertEqual(pin, {"nixpkgs_rev": revision, "nixpkgs_hash": source_hash, "go_attr": "go_1_28", "go_version": "1.28.1"})
            with mock.patch.dict(candidate_pin.__globals__, {"run": mock.Mock(side_effect=answers)}):
                with self.assertRaisesRegex(ValueError, "no stable Go"):
                    candidate_pin(source, {"go_version": "1.28.2", "nixpkgs_rev": "b" * 40})
            same_revision = mock.Mock(return_value=answers[0])
            with mock.patch.dict(candidate_pin.__globals__, {"run": same_revision}):
                self.assertEqual(candidate_pin(source, pin), pin)
            self.assertEqual(same_revision.call_count, 1)

    def test_discovery_failure_preserves_pin_and_success_is_idempotent(self):
        update = self.helper["update"]
        candidate = {"go_version": "1.28.1"}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "toolchain.json"
            original = '{"go_version": "1.27.1"}\n'
            path.write_text(original)
            with mock.patch.dict(update.__globals__, {"candidate_pin": mock.Mock(side_effect=ValueError("unavailable"))}):
                with self.assertRaises(ValueError):
                    update(Path(temp), path)
            self.assertEqual(path.read_text(), original)
            with mock.patch.dict(update.__globals__, {"candidate_pin": mock.Mock(return_value=candidate)}):
                self.assertTrue(update(Path(temp), path))
                self.assertFalse(update(Path(temp), path))


class UpdateUpstreamsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text()
        cls.package_workflow = (ROOT / ".github/workflows/update-package.yml").read_text()
        cls.updater = UPDATER.read_text()
        cls.detector = PACKAGE_DETECTOR.read_text()

    def test_github_cli_uses_its_pinned_toolchain_in_packages_and_overlay(self) -> None:
        flake = (ROOT / "flake.nix").read_text()
        package = (ROOT / "pkgs/github-cli/default.nix").read_text()
        toolchain = (ROOT / "pkgs/github-cli/toolchain.nix").read_text()
        self.assertIn("import ./pkgs/github-cli/toolchain.nix", flake)
        self.assertIn("goPkgs.buildGoModule.override { inherit go; }", toolchain)
        self.assertIn("assert go.version == pin.go_version;", toolchain)
        self.assertEqual(flake.count("githubCliGoModule = githubCliGoModuleFor system;"), 2)
        self.assertIn("githubCliGoModule rec {", package)
        self.assertNotIn("buildGo126Module", package)
        self.assertIn('"$out/bin/gh" --version', package)
        block = self.updater.split("update_pinned_go_package() {", 1)[1].split("\nblock_github_cli()", 1)[0]
        self.assertLess(block.index("update-github-cli-toolchain"), block.index('"$latest_version" == "$current_version"'))
        self.assertIn('supabase/cli apps/cli-go', self.updater)
        self.assertEqual(flake.count("supabaseCliGoModule = supabaseCliGoModuleFor system;"), 2)
        self.assertNotIn("hermes-agent.inputs.nixpkgs", flake)

    def test_go_toolchain_mismatch_is_reported_and_hash_is_restored(self) -> None:
        helpers = self.updater[self.updater.index("nix_string_value() {"):self.updater.index("json_field() {")]
        discovery = self.updater[self.updater.index("discover_nix_fixed_hash() {"):self.updater.index("update_tagged_go_package() {")]
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "default.nix"
            original = '  vendorHash = "sha256-original=";\n'
            package.write_text(original)
            diagnostic = "go.mod requires go >= 1.27.0 (running go 1.26.5; GOTOOLCHAIN=local)"
            result = subprocess.run(
                ["bash", "-c", f'''
set -euo pipefail
{helpers}
{discovery}
nix() {{ printf '%s\\n' {shlex.quote('       > go: ' + diagnostic)} >&2; return 1; }}
discover_nix_fixed_hash github-cli {shlex.quote(str(package))} vendorHash
'''], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(diagnostic, result.stderr)
            self.assertEqual(package.read_text(), original)

    def test_focused_contract_tests_gate_the_matching_builds(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        for workflow in (ci, self.workflow):
            step = workflow_step(workflow, "Check Codex packaging contracts")
            self.assertIn("-k CodexPackageContractTests", step)
            self.assertIn("-k CodexCargoHashUpdaterTests", step)
            self.assertNotIn("continue-on-error", step)
        controls = workflow_step(ci, "Check update workflow contracts")
        for suite in ("UpdateUpstreamsWorkflowTests", "PackageDetectorTests", "UpdateArtifactTests", "GitHubCliToolchainTests"):
            self.assertIn(f"-k {suite}", controls)
        go_checks = workflow_step(self.workflow, "Check GitHub CLI toolchain contracts")
        self.assertIn("-k GitHubCliToolchainTests", go_checks)
        self.assertNotIn("continue-on-error", go_checks)
        for package, label, command in (
            ("hermes-agent", "Hermes", "python3 -m unittest discover -s tests -p test_hermes_registration_lifecycle.py -v"),
            ("camofox-browser", "Camofox", "python3 -m unittest discover -s tests -p test_update_upstreams.py -k ExternalPackageContractTests.test_camofox -v"),
        ):
            step = workflow_step(ci, f"Check {label} packaging contracts")
            self.assertIn(f"matrix.package == '{package}'", step)
            self.assertIn(command, step)
            self.assertLess(ci.index(step), ci.index("      - name: Build package\n"))
            if package == "camofox-browser":
                candidate = workflow_step(self.workflow, "Check Camofox candidate contracts")
                self.assertIn(command, candidate)
                self.assertLess(self.workflow.index(candidate), self.workflow.index("      - name: Build verified Camofox update\n"))
            else:
                contract = workflow_step(self.package_workflow, "Check Hermes packaging contracts")
                self.assertIn(command, contract)
                self.assertIn("inputs.package == 'hermes-agent'", contract)
                self.assertLess(self.package_workflow.index(contract), self.package_workflow.index("./scripts/build-package"))

    def test_nested_hermes_lock_change_selects_only_hermes(self) -> None:
        start = self.updater.index(
            'if [[ "$update_mode" != "codex-only"', self.updater.index("changed=()")
        )
        selection = self.updater[start:self.updater.index("\nfi", start) + 3]
        for mode, changed in (("without-codex", True), ("without-codex", False), ("codex-only", True)):
            with self.subTest(mode=mode, changed=changed), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                lock = {"nodes": {
                    "hermes-agent": {"locked": {"rev": "same"}, "inputs": {"nixpkgs": "hermes-nixpkgs"}},
                    "hermes-nixpkgs": {"locked": {"rev": "before"}},
                }}
                before = repo / "before.lock"
                before.write_text(json.dumps(lock))
                if changed:
                    lock["nodes"]["hermes-nixpkgs"]["locked"]["rev"] = "after"
                (repo / "flake.lock").write_text(json.dumps(lock))
                result = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", f"""
update_mode={shlex.quote(mode)}
before_lock={shlex.quote(str(before))}
hermes_before=same
hermes_after=same
add_changed_package() {{ printf '%s\\n' "$1"; }}
{selection}
"""], cwd=repo, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = ["hermes-agent"] if changed and mode != "codex-only" else []
                self.assertEqual(result.stdout.splitlines(), expected)

    def test_verified_codex_commit_precedes_unrelated_updates(self) -> None:
        codex_update = workflow_step(self.workflow, "Update Codex input")
        codex_build = workflow_step(self.workflow, "Build verified Codex update")
        codex_commit = workflow_step(
            self.workflow, "Commit and push verified Codex update"
        )
        remaining_update = workflow_step(self.package_workflow, "Update selected upstream input")
        remaining_build = workflow_step(self.package_workflow, "Build selected package")

        self.assertIn("./scripts/update-upstream-inputs --codex-only", codex_update)
        self.assertIn("./scripts/build-package codex", codex_build)
        self.assertIn("git push", codex_commit)
        self.assertIn(
            './scripts/update-upstream-inputs --package "${{ inputs.package }}"', remaining_update
        )
        self.assertLess(self.workflow.index(codex_build), self.workflow.index(codex_commit))
        self.assertLess(
            self.workflow.index(codex_commit), self.workflow.index("  update-remaining:")
        )
        self.assertLess(
            self.package_workflow.index(remaining_update), self.package_workflow.index(remaining_build)
        )
        remaining_job = workflow_job(self.workflow, "update-remaining")
        self.assertIn("- publish-codex", remaining_job)

    def test_prepare_jobs_are_read_only_and_publish_jobs_are_narrow(self) -> None:
        prepare_codex = workflow_job(self.workflow, "prepare-codex")
        publish_codex = workflow_job(self.workflow, "publish-codex")
        prepare_remaining = workflow_job(self.package_workflow, "prepare")
        publish_remaining = workflow_job(self.package_workflow, "publish")

        for job in (prepare_codex, prepare_remaining, workflow_job(self.workflow, "prepare-github-cli"), workflow_job(self.workflow, "prepare-camofox")):
            self.assertIn("permissions:\n      contents: read", job)
            self.assertIn("persist-credentials: false", job)
            self.assertNotIn("git push", job)
        for job in (publish_codex, publish_remaining, workflow_job(self.workflow, "publish-github-cli"), workflow_job(self.workflow, "publish-camofox")):
            self.assertIn("permissions:\n      contents: write", job)
            self.assertIn("update-artifact verify-apply", job)
            self.assertIn("git push origin HEAD:refs/heads/main", job)
            self.assertNotIn("build-package", job)
            self.assertNotIn("CACHIX_AUTH_TOKEN", job)

    def test_secrets_are_scoped_to_the_steps_that_need_them(self) -> None:
        self.assertEqual(self.workflow.count("secrets.CACHIX_AUTH_TOKEN"), 10)
        self.assertEqual(self.package_workflow.count("secrets.CACHIX_AUTH_TOKEN"), 3)
        for step_name in ("Detect Cachix configuration", "Configure Cachix"):
            for step in re.findall(
                rf"      - name: {step_name}\n.*?(?=\n      - name: |\n  [a-z0-9-]+:|\Z)",
                self.workflow,
                re.DOTALL,
            ):
                self.assertIn("secrets.CACHIX_AUTH_TOKEN", step)
        for step_name in ("Build verified Codex update", "Build verified GitHub CLI update", "Build verified Camofox update"):
            step = workflow_step(self.workflow, step_name)
            self.assertIn("secrets.CACHIX_AUTH_TOKEN", step)
            self.assertIn("REQUIRE_CACHIX_PUSH: 1", step)
        selected = workflow_step(self.package_workflow, "Build selected package")
        self.assertIn("secrets.CACHIX_AUTH_TOKEN", selected)
        self.assertIn("REQUIRE_CACHIX_PUSH: 1", selected)

    def test_remaining_updater_authenticates_github_api_requests(self) -> None:
        step = workflow_step(self.package_workflow, "Update selected upstream input")
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", step)

    def test_codex_prepare_failure_does_not_starve_remaining_updates(self) -> None:
        remaining_job = workflow_job(self.workflow, "update-remaining")
        self.assertIn("- prepare-codex", remaining_job)
        self.assertIn("- publish-codex", remaining_job)
        # The only continuation gate is cancellation, including when a prior
        # publisher refused a stale base. Exact-base gates stay in each lane.
        self.assertIn("if: ${{ always() && !cancelled() }}", remaining_job)

    def test_remaining_publisher_survives_skipped_codex_noop(self) -> None:
        publish_remaining = workflow_job(self.package_workflow, "publish")
        self.assertIn("needs: prepare", publish_remaining)
        self.assertIn(
            "if: ${{ always() && !cancelled() && "
            "needs.prepare.result == 'success' && "
            "needs.prepare.outputs.changed == 'true' }}",
            publish_remaining,
        )

    def test_every_action_is_pinned_to_a_full_sha_with_version_comment(self) -> None:
        uses_lines = re.findall(
            r"^\s*uses:\s+([^@\s]+)@([^\s]+)(.*)$", self.workflow + self.package_workflow, re.MULTILINE
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
            "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        }
        for action, sha in expected.items():
            self.assertIn(f"uses: {action}@{sha}", self.workflow)

    def test_dependabot_builds_without_cache_publication_and_smokes_artifact_pair(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn(
            "CACHIX_AUTH_TOKEN: ${{ github.event.pull_request.user.login != 'dependabot[bot]' && secrets.CACHIX_AUTH_TOKEN || '' }}",
            ci,
        )
        self.assertIn(
            "REQUIRE_CACHIX_PUSH: ${{ github.event.pull_request.user.login == 'dependabot[bot]' && '0' || '1' }}",
            ci,
        )
        self.assertIn('run: NIX_BUILD_MONITOR=1 ./scripts/build-package "${{ matrix.package }}"', ci)
        for direction in ("Upload", "Download"):
            smoke = workflow_step(ci, f"{direction} artifact transport smoke")
            production = workflow_step(self.workflow, f"{direction} Codex publication artifact")
            self.assertEqual(
                re.search(r"uses: (\S+)", smoke).group(1),
                re.search(r"uses: (\S+)", production).group(1),
            )
            self.assertNotIn("continue-on-error", smoke)
        self.assertIn("run: cmp ", workflow_step(ci, "Verify artifact transport smoke"))

    def test_publish_jobs_revalidate_artifact_and_base_before_push(self) -> None:
        jobs = [workflow_job(self.workflow, name) for name in ("publish-codex", "publish-github-cli", "publish-camofox")]
        jobs.append(workflow_job(self.package_workflow, "publish"))
        for job in jobs:
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
        codex_block = self.updater[codex_mode:self.updater.index("  github-cli-only)\n", codex_mode)]
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
        self.assertNotIn("block_camofox", remaining_block)
        self.assertNotIn("run_block codex_ref", remaining_block)
        self.assertNotIn("run_block github_cli", remaining_block)

    def test_github_cli_failure_cannot_gate_earlier_publications(self) -> None:
        prepare = workflow_job(self.workflow, "prepare-github-cli")
        publish = workflow_job(self.workflow, "publish-github-cli")
        self.assertIn("needs: update-remaining", prepare)
        self.assertIn("if: ${{ always() && !cancelled() }}", prepare)
        matrix = workflow_job(self.workflow, "update-remaining")
        self.assertIn("fail-fast: false", matrix)
        self.assertIn("max-parallel: 1", matrix)
        self.assertIn("uses: ./.github/workflows/update-package.yml", matrix)
        packages = re.search(r"package: \[([^]]+)\]", matrix).group(1).split(", ")
        self.assertEqual(set(packages), runpy.run_path(str(ARTIFACT_TOOL))["PHASE_PACKAGES"]["remaining"])
        self.assertIn("--github-cli-only", prepare)
        self.assertIn("--phase github-cli", prepare)
        self.assertIn("--phase github-cli", publish)
        self.assertIn("needs.prepare-github-cli.result == 'success'", publish)
        self.assertIn("needs.prepare-github-cli.outputs.changed == 'true'", publish)
        self.assertNotIn("continue-on-error", prepare)
        for lane in ("prepare-codex", "publish-codex", "update-remaining"):
            self.assertNotIn("needs.prepare-github-cli", workflow_job(self.workflow, lane))
        self.assertLess(self.workflow.index("  update-remaining:"), self.workflow.index("  prepare-github-cli:"))

    def test_github_cli_dependency_failure_is_isolated_and_rolled_back(self) -> None:
        selection = self.updater.index(
            'case "$update_mode" in', self.updater.index("block_symphony_ts()")
        )
        blocks = re.findall(r"^block_([a-z_]+)\(\)", self.updater, re.MULTILINE)
        stubs = "\n".join(f"block_{name}() {{ return 0; }}" for name in blocks)
        stubs += '''
lock_fingerprint() { printf 'unchanged'; }
block_archon() { printf 'updated\\n' > "$archon_package_file"; }
block_github_cli() {
  printf 'broken-new-dependencies\\n' > "$github_cli_package_file"
  printf 'broken-new-toolchain\\n' > "$github_cli_toolchain_file"
  return 1
}
'''
        for mode, toolchain_only in (("--without-codex", False), ("--github-cli-only", False), ("--github-cli-only", True)):
            refresh = 'block_github_cli() { printf "updated\\n" > "$github_cli_toolchain_file"; }' if toolchain_only else ""
            script = self.updater[:selection] + stubs + "\n" + refresh + "\n" + self.updater[selection:]
            with self.subTest(mode=mode, toolchain_only=toolchain_only), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                for name in re.findall(r'^\w+_(?:package|toolchain)_file="([^"]+)"', self.updater, re.MULTILINE):
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("original\n")
                (repo / "flake.lock").write_text("original\n")
                (repo / "flake.nix").write_text("original\n")
                output = repo / "outputs"
                result = subprocess.run(
                    ["bash", "-c", script, "updater-fixture", mode], cwd=repo,
                    env={**os.environ, "GITHUB_OUTPUT": str(output), "UPDATE_PACKAGES_FILE": ".changed-packages"},
                    capture_output=True, text=True,
                )
                self.assertEqual((repo / "pkgs/github-cli/default.nix").read_text(), "original\n")
                self.assertEqual((repo / "pkgs/github-cli/toolchain.json").read_text(), "updated\n" if toolchain_only else "original\n")
                self.assertEqual((repo / "flake.lock").read_text(), "original\n")
                if mode == "--without-codex" or toolchain_only:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((repo / ".changed-packages").read_text(), "github-cli\n" if toolchain_only else "archon\n")
                    self.assertIn("changed=true", output.read_text())
                else:
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("required upstream update blocks failed: github_cli", result.stderr)
                    self.assertFalse((repo / ".changed-packages").exists())
                    self.assertFalse(output.exists())

    def test_camofox_failure_is_isolated_and_rolled_back(self) -> None:
        prepare = workflow_job(self.workflow, "prepare-camofox")
        self.assertIn("- prepare-github-cli", prepare)
        self.assertIn("- publish-github-cli", prepare)
        self.assertIn("if: ${{ always() && !cancelled() }}", prepare)
        self.assertIn("--camofox-only", prepare)
        self.assertIn("./scripts/build-package camofox-browser", prepare)
        self.assertIn("-k ExternalPackageContractTests.test_camofox", prepare)
        for lane in ("update-remaining", "prepare-github-cli", "publish-github-cli"):
            self.assertNotIn("needs.prepare-camofox", workflow_job(self.workflow, lane))
        selection = self.updater.index('case "$update_mode" in', self.updater.index("block_symphony_ts()"))
        blocks = re.findall(r"^block_([a-z_]+)\(\)", self.updater, re.MULTILINE)
        stubs = "\n".join(f"block_{name}() {{ return 0; }}" for name in blocks)
        stubs += '''
lock_fingerprint() { printf 'unchanged'; }
block_archon() { printf 'updated\\n' > "$archon_package_file"; }
block_camofox() { printf 'broken\\n' > "$camofox_package_file"; return 1; }
'''
        script = self.updater[:selection] + stubs + "\n" + self.updater[selection:]
        for mode in ("--without-codex", "--camofox-only"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                for name in re.findall(r'^\w+_(?:package|toolchain)_file="([^"]+)"', self.updater, re.MULTILINE):
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("original\n")
                (repo / "flake.lock").write_text("original\n")
                (repo / "flake.nix").write_text("original\n")
                output = repo / "outputs"
                result = subprocess.run(["bash", "-c", script, "fixture", mode], cwd=repo,
                    env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True)
                self.assertEqual((repo / "pkgs/camofox-browser/default.nix").read_text(), "original\n")
                if mode == "--without-codex":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((repo / ".changed-packages").read_text(), "archon\n")
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertFalse(output.exists())
                    self.assertFalse((repo / ".changed-packages").exists())

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

    def test_failed_remaining_blocks_make_the_updater_fail_loud(self) -> None:
        guard_start = self.updater.index(
            'if [[ "$update_mode" != "codex-only" ]]',
            self.updater.index('echo "::endgroup::"', self.updater.index("# Final summary")),
        )
        guard_end = self.updater.index(
            "\n\nif ((${#changed[@]} > 0))", guard_start
        )
        guard = self.updater[guard_start:guard_end]
        result = subprocess.run(
            ["bash", "-c", f"""
set -euo pipefail
update_mode=github-cli-only
declare -A block_status=([github_cli]=FAILED [camofox]=OK)
{guard}
"""],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("required upstream update blocks failed: github_cli", result.stderr)

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

    def test_codex_and_hermes_upgrade_contracts_are_version_independent(self) -> None:
        flake = (ROOT / "flake.nix").read_text()
        self.assertRegex(flake, r'github:openai/codex/rust-v\d+\.\d+\.\d+')
        self.assertNotIn("registration_lifecycle", flake)
        self.assertIn('#![recursion_limit = "256"]', flake)

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
        for args, error in ((["--unknown-mode"], "usage:"),
                            (["--package", "codex"], "unsupported isolated package"),
                            (["--package", "neurobooks"], "unsupported isolated package")):
            with self.subTest(args=args):
                result = subprocess.run([str(UPDATER), *args], cwd=ROOT, check=False,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn(error, result.stderr)


class CodexPackageContractTests(unittest.TestCase):
    def test_codex_recursion_patch_handles_mcp_removal_without_skipping_required_checks(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        marker = "codexRecursionLimitPatch = ''"
        patch_start = flake_source.index(marker) + len(marker)
        patch_end = flake_source.index("'';", patch_start)
        helper = flake_source[patch_start:patch_end]
        self.assertIn("${codexRecursionLimitPatch}", flake_source)
        patch = helper
        attribute = '#![recursion_limit = "256"]\n'
        cases = (
            ("removed-mcp", {}, True),
            ("retained-mcp", {"lib.rs": attribute, "main.rs": attribute}, True),
            ("regressed-mcp", {"lib.rs": "", "main.rs": attribute}, False),
            ("partial-mcp", {"lib.rs": attribute}, False),
            ("missing-exec", {}, False),
            ("missing-cli", {}, False),
            ("missing-chatgpt", {}, False),
        )
        for name, mcp_sources, succeeds in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                for crate, target in (("exec", "lib.rs"), ("cli", "main.rs"), ("chatgpt", "lib.rs")):
                    if name != f"missing-{crate}":
                        path = source / crate / "src" / target
                        path.parent.mkdir(parents=True)
                        path.write_text("// crate root\n")
                for target, content in mcp_sources.items():
                    path = source / "mcp-server" / "src" / target
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                for _ in range(2 if succeeds else 1):
                    result = subprocess.run(
                        ["bash", "-euo", "pipefail", "-c", patch],
                        cwd=source,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                if succeeds:
                    for target in ("exec/src/lib.rs", "cli/src/main.rs", "chatgpt/src/lib.rs"):
                        self.assertEqual(
                            (source / target).read_text(), attribute + "// crate root\n"
                        )

    def test_codex_i18n_patch_sorts_codegen_arguments(self) -> None:
        patch_path = ROOT / "patches/i18n-embed-fl-stable-arguments.patch"
        patch_source = patch_path.read_text()
        original = "\n".join(
            line[1:] for line in patch_source.splitlines()
            if line.startswith(" ") or (line.startswith("-") and not line.startswith("---"))
        ) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "src/lib.rs"
            source.parent.mkdir()
            source.write_text(original)
            subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch_path)],
                cwd=temporary, check=True, capture_output=True,
            )
            changed = source.read_text()
            self.assertIn("ordered_args.sort_by_key(|(key, _)| key.value());", changed)
            self.assertIn("for (key, value) in ordered_args", changed)
            self.assertNotIn("for (key, value) in &specified_args", changed)
        flake_source = (ROOT / "flake.nix").read_text()
        self.assertIn('"$cargoDepsCopy/i18n-embed-fl-0.9.4"', flake_source)
        self.assertIn("${./patches/i18n-embed-fl-stable-arguments.patch}", flake_source)

    def test_codex_registry_crates_use_the_official_static_download_endpoint(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        self.assertIn(
            '"https://static.crates.io/crates/${crateName}/${crateName}-${crateVersion}.crate"',
            flake_source,
        )
        self.assertIn(
            "fetchurl = codexCrateFetcherFor pkgs;",
            flake_source,
        )
        self.assertNotIn(
            "cargoDeps = pkgs.rustPlatform.importCargoLock {",
            flake_source,
        )

    def test_reproducibility_check_requires_explicit_manual_selection(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        step = workflow_step(ci, "Check package reproducibility")
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.check_reproducibility", step)
        self.assertIn("nix build --rebuild --no-link", step)
        self.assertNotIn("continue-on-error", step)

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

    def test_codex_post_patch_targets_exec_cli_and_chatgpt_with_shared_helper(self) -> None:
        flake_source = (ROOT / "flake.nix").read_text()
        patch_start = flake_source.index("codexRecursionLimitPatch = ''")
        patch_end = flake_source.index("'';", patch_start)
        helper = flake_source[patch_start:patch_end]
        self.assertIn("add_recursion_limit()", helper)
        self.assertIn("add_recursion_limit exec/src/lib.rs", helper)
        self.assertIn("add_recursion_limit cli/src/main.rs", helper)
        self.assertIn("add_recursion_limit chatgpt/src/lib.rs", helper)
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
    def test_go_package_source_hash_prefetches_unpacked_archive(self) -> None:
        source = (ROOT / "scripts" / "update-upstream-inputs").read_text()
        start = source.index("update_tagged_go_package()")
        end = source.index("\n}\n\nupdate_tagged_npm_package", start)
        block = source[start:end]
        self.assertIn(
            'prefetch_json true "https://github.com/${repo}/archive/${latest_tag}.tar.gz"',
            block,
        )
        self.assertNotIn(
            'prefetch_json false "https://github.com/${repo}/archive/${latest_tag}.tar.gz"',
            block,
        )

    def test_camofox_candidate_copy_makes_store_source_writable(self) -> None:
        source = (ROOT / "scripts" / "update-upstream-inputs").read_text()
        start = source.index("block_camofox()")
        end = source.index("\n}\n\n# Block: DevSpace", start)
        block = source[start:end]
        self.assertIn(
            'cp -R --no-preserve=mode "$camofox_src_path"/. "$camofox_candidate_path"/',
            block,
        )

    def test_camofox_repair_runs_before_npm_prefetch(self) -> None:
        source = (ROOT / "scripts" / "update-upstream-inputs").read_text()
        repair = source.index("repair-camofox-package-lock.py")
        prefetch = source.index("prefetch-npm-deps", repair)
        self.assertLess(repair, prefetch)
        package = (ROOT / "pkgs" / "camofox-browser" / "default.nix").read_text()
        self.assertIn("repair-camofox-package-lock.py", package)

    def test_camofox_compat_patch_handles_legacy_current_and_native_layouts(
        self,
    ) -> None:
        namespace = runpy.run_path(str(CAMOFOX_COMPAT_PATCH))
        transforms = namespace["TRANSFORMS"]
        native_user_nav_health = namespace["NATIVE_USER_NAV_HEALTH"]
        self.assertEqual(
            [transform.label for transform in transforms],
            [
                "install-dir",
                "camoufox-path",
                "default-addons",
                "session-grace",
                "request-timeout",
                "idle-shutdown",
                "health-state",
                "browser-launch",
                "active-health-probe",
            ],
        )

        def fixture(profile: str) -> dict[str, str]:
            sources: dict[str, list[str]] = {"pkgman": [], "server": []}
            for transform in transforms:
                variants = {variant.name: variant for variant in transform.variants}
                if profile == "native-1.16" and transform.label == "idle-shutdown":
                    name = "native-1.16"
                elif profile in ("native-1.14", "native-1.16"):
                    name = (
                        "native-1.14"
                        if "native-1.14" in variants
                        else "native"
                        if "native" in variants
                        else "shared"
                    )
                elif profile == "current-ce3" and transform.label == "idle-shutdown":
                    name = "native"
                else:
                    name = "legacy" if "legacy" in variants else "shared"
                sources[transform.target].append(variants[name].before)
            if profile in ("native-1.14", "native-1.16"):
                sources["server"].append(native_user_nav_health)
            return {target: "\n\n".join(parts) for target, parts in sources.items()}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkgman = root / "pkgman.js"
            server = root / "server.js"
            for profile in ("legacy", "current-ce3", "native-1.14", "native-1.16"):
                with self.subTest(profile=profile):
                    sources = fixture(profile)
                    pkgman.write_text(sources["pkgman"])
                    server.write_text(sources["server"])
                    result = subprocess.run(
                        ["python3", str(CAMOFOX_COMPAT_PATCH), str(pkgman), str(server)],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    patched_pkgman = pkgman.read_text()
                    patched_server = server.read_text()
                    self.assertIn("CAMOUFOX_INSTALL_DIR", patched_pkgman)
                    self.assertIn("now - session.lastAccess > 120000", patched_server)
                    self.assertIn("const localFloorMs = 120000;", patched_server)
                    self.assertIn("let activeHealthProbeInFlight = false;", patched_server)
                    self.assertIn(
                        "if (!browser || healthState.isRecovering || "
                        "activeHealthProbeInFlight) return;",
                        patched_server,
                    )
                    self.assertIn(
                        "if (sessions.size === 0 && getTotalTabCount() === 0) return;",
                        patched_server,
                    )
                    if profile == "native-1.16":
                        self.assertIn(
                            "if (browserIdleTimer || sessions.size > 0 || !browser || BROWSER_IDLE_TIMEOUT_MS <= 0) return;",
                            patched_server,
                        )
                    if profile in ("native-1.14", "native-1.16"):
                        self.assertIn("userNavHealth.clear();", patched_server)
                        self.assertIn(
                            "browser.newContext({ viewport: null })", patched_server
                        )
                        self.assertNotIn(
                            "healthState.consecutiveNavFailures = 0;", patched_server
                        )
                    else:
                        self.assertIn(
                            "healthState.consecutiveNavFailures = 0;", patched_server
                        )

        package = (ROOT / "pkgs" / "camofox-browser" / "default.nix").read_text()
        self.assertIn("scripts/patch-camofox-browser.py", package)
        self.assertEqual(package.count("--set CAMOFOX_DISABLE_DEFAULT_ADDONS 1"), 1)

    def test_camofox_compat_preflight_reports_all_drift_without_mutation(
        self,
    ) -> None:
        namespace = runpy.run_path(str(CAMOFOX_COMPAT_PATCH))
        transforms = namespace["TRANSFORMS"]
        sources: dict[str, list[str]] = {"pkgman": [], "server": []}
        selected = {}
        for transform in transforms:
            variants = {variant.name: variant for variant in transform.variants}
            name = (
                "native-1.14"
                if "native-1.14" in variants
                else "native"
                if "native" in variants
                else "shared"
            )
            selected[transform.label] = variants[name]
            sources[transform.target].append(variants[name].before)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkgman = root / "pkgman.js"
            server = root / "server.js"
            pkgman_source = "\n\n".join(sources["pkgman"]).replace(
                selected["install-dir"].before, "export const INSTALL_DIR = drift;"
            )
            server_source = "\n\n".join(sources["server"])
            for label in ("session-grace", "health-state", "active-health-probe"):
                server_source = server_source.replace(
                    selected[label].before, f"/* drift: {label} */"
                )
            server_source += "\n\n" + selected["request-timeout"].before
            pkgman.write_text(pkgman_source)
            server.write_text(server_source)
            before_pkgman = pkgman.read_bytes()
            before_server = server.read_bytes()

            result = subprocess.run(
                ["python3", str(CAMOFOX_COMPAT_PATCH), str(pkgman), str(server)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            for label in (
                "install-dir",
                "session-grace",
                "request-timeout",
                "health-state",
                "active-health-probe",
            ):
                self.assertIn(label, result.stderr)
            self.assertIn("shared=2", result.stderr)
            self.assertEqual(pkgman.read_bytes(), before_pkgman)
            self.assertEqual(server.read_bytes(), before_server)

    def test_camofox_compat_preflight_rejects_mixed_health_profile(self) -> None:
        namespace = runpy.run_path(str(CAMOFOX_COMPAT_PATCH))
        transforms = namespace["TRANSFORMS"]
        sources: dict[str, list[str]] = {"pkgman": [], "server": []}
        for transform in transforms:
            variants = {variant.name: variant for variant in transform.variants}
            if transform.label == "browser-launch":
                name = "legacy"
            else:
                name = (
                    "native-1.14"
                    if "native-1.14" in variants
                    else "native"
                    if "native" in variants
                    else "shared"
                )
            sources[transform.target].append(variants[name].before)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkgman = root / "pkgman.js"
            server = root / "server.js"
            pkgman.write_text("\n\n".join(sources["pkgman"]))
            server.write_text("\n\n".join(sources["server"]))
            before_pkgman = pkgman.read_bytes()
            before_server = server.read_bytes()

            result = subprocess.run(
                ["python3", str(CAMOFOX_COMPAT_PATCH), str(pkgman), str(server)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "health-profile coherence (health-state=native-1.14, "
                "browser-launch=legacy, active-health-probe=native-1.14)",
                result.stderr,
            )
            self.assertEqual(pkgman.read_bytes(), before_pkgman)
            self.assertEqual(server.read_bytes(), before_server)

    def test_camofox_compat_preflight_requires_single_native_user_nav_health(
        self,
    ) -> None:
        namespace = runpy.run_path(str(CAMOFOX_COMPAT_PATCH))
        transforms = namespace["TRANSFORMS"]
        declaration = namespace["NATIVE_USER_NAV_HEALTH"]
        sources: dict[str, list[str]] = {"pkgman": [], "server": []}
        for transform in transforms:
            variants = {variant.name: variant for variant in transform.variants}
            name = (
                "native-1.14"
                if "native-1.14" in variants
                else "native"
                if "native" in variants
                else "shared"
            )
            sources[transform.target].append(variants[name].before)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkgman = root / "pkgman.js"
            server = root / "server.js"
            for count in (0, 2):
                with self.subTest(count=count):
                    pkgman.write_text("\n\n".join(sources["pkgman"]))
                    server.write_text(
                        "\n\n".join(sources["server"] + [declaration] * count)
                    )
                    before_pkgman = pkgman.read_bytes()
                    before_server = server.read_bytes()

                    result = subprocess.run(
                        [
                            "python3",
                            str(CAMOFOX_COMPAT_PATCH),
                            str(pkgman),
                            str(server),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        f"user-nav-health declaration (native-1.14={count})",
                        result.stderr,
                    )
                    self.assertEqual(pkgman.read_bytes(), before_pkgman)
                    self.assertEqual(server.read_bytes(), before_server)

    def test_camofox_package_has_reproducible_candidate_pins(self) -> None:
        package = (ROOT / "pkgs" / "camofox-browser" / "default.nix").read_text()
        # Historical source-layout contracts are exercised by the fixtures
        # below. A live package pin must not freeze the updater at that release.
        self.assertRegex(package, r'camofoxBrowserVersion = "[0-9]+\.[0-9]+\.[0-9]+";')
        self.assertRegex(package, r'camofoxBrowserRev = "[0-9a-f]{40}";')
        for name in ("camofoxBrowserHash", "camofoxBrowserNpmDepsHash", "camoufoxEngineHash"):
            self.assertRegex(package, rf'{name} = "sha256-[A-Za-z0-9+/]{{43}}=";')
        # Upstream can publish a new engine asset under a differently named
        # release (for example font-bundle-v1). The download uses both pins.
        self.assertRegex(package, r'camoufoxEngineVersion = "[0-9]+\.[0-9]+\.[0-9]+[^"\n]*";')
        self.assertRegex(package, r'camoufoxEngineReleaseTag = "[^"\s]+";')
        self.assertIn(
            '/releases/download/${camoufoxEngineReleaseTag}/camoufox-${camoufoxEngineVersion}-lin.x86_64.zip',
            package,
        )

    def test_camofox_updater_preserves_independent_release_tag_and_asset_version(self) -> None:
        source = UPDATER.read_text()
        functions = "\n".join(
            re.search(rf"^{name}\(\) \{{\n.*?^\}}", source, re.MULTILINE | re.DOTALL).group(0)
            for name in (
                "nix_string_value", "replace_nix_string", "json_field",
                "latest_camoufox_engine_asset", "block_camofox",
            )
        )
        release_tag = "font-bundle-v1"
        engine_version = "152.0.4-beta.31"
        asset_name = f"camoufox-{engine_version}-lin.x86_64.zip"
        asset_url = f"https://github.com/daijro/camoufox/releases/download/{release_tag}/{asset_name}"
        engine_hash = "sha256-" + "A" * 43 + "="
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "candidate.nix"
            original = (ROOT / "pkgs/camofox-browser/default.nix").read_text()
            # Make the transition independent of whichever pin is current.
            original = re.sub(r'(camoufoxEngineReleaseTag = ")[^"]+', r'\g<1>previous-release', original)
            original = re.sub(r'(camoufoxEngineVersion = ")[^"]+', r'\g<1>1.0.0', original)
            package.write_text(original)
            (root / "releases.json").write_text(json.dumps([{
                "tag_name": release_tag,
                "assets": [{"name": asset_name, "browser_download_url": asset_url}],
            }]))
            script = "set -euo pipefail\n" + functions + r'''
camofox_package_file=candidate.nix
github_api_get() { cat releases.json; }
latest_git_head() { nix_string_value camofoxBrowserRev "$camofox_package_file"; }
prefetch_json() {
  [[ "$1" == false && "$2" == "$EXPECTED_ASSET_URL" ]] || return 1
  printf '%s\n' "$2" >> prefetched-urls
  printf '{"hash":"%s"}\n' "$EXPECTED_ENGINE_HASH"
}
block_camofox
block_camofox
'''
            result = subprocess.run(
                ["bash", "-c", script], cwd=root,
                env={**os.environ, "EXPECTED_ASSET_URL": asset_url, "EXPECTED_ENGINE_HASH": engine_hash},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = original
            for name, value in (
                ("camoufoxEngineReleaseTag", release_tag),
                ("camoufoxEngineVersion", engine_version),
                ("camoufoxEngineHash", engine_hash),
            ):
                expected = re.sub(rf'({name} = ")[^"]+', rf'\g<1>{value}', expected)
            # Only the engine's coherent tag/version/hash tuple changes;
            # browser version, source revision and dependency hashes stay put.
            self.assertEqual(package.read_text(), expected)
            self.assertEqual((root / "prefetched-urls").read_text(), asset_url + "\n")

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

    def test_go_packages_use_independent_pinned_builders(self) -> None:
        flake = (ROOT / "flake.nix").read_text()
        self.assertNotIn("hermes-agent.inputs.nixpkgs", flake)
        for package, builder in (("github-cli", "githubCliGoModule"), ("supabase-cli", "supabaseCliGoModule")):
            with self.subTest(package=package):
                source = (ROOT / "pkgs" / package / "default.nix").read_text()
                self.assertIn(builder, source)
                self.assertNotIn("buildGo125Module", source)

    def test_freellmapi_keeps_server_client_scope(self) -> None:
        source = (ROOT / "pkgs" / "freellmapi" / "default.nix").read_text()
        self.assertIn("npm run build:server", source)
        self.assertIn("npm run build -w client", source)
        self.assertNotIn("\nnpm run build -w cli\n", source)
        self.assertIn("server/dist server/package.json server/node_modules", source)
        self.assertIn("client/dist client/package.json", source)
        self.assertIn("doInstallCheck = true;", source)
        self.assertIn("await import('ajv/dist/2020.js')", source)
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
    def test_codex_literal_pin_changes_are_narrow_but_packaging_changes_are_not(self) -> None:
        original = (ROOT / "flake.nix").read_text()
        pins = re.sub(
            r'github:openai/codex/rust-v[0-9.]+',
            "github:openai/codex/rust-v9.9.9", original,
        )
        pins = pins.replace(
            '    codexCargoOutputHashes = lib: {\n',
            '    codexCargoOutputHashes = lib: {\n'
            '      "new-dependency-1.0.0" = "sha256-' + 'A' * 43 + '=";\n',
        )
        pins = re.sub(
            r'(RUSTY_V8_ARCHIVE = pkgs.fetchurl \{.*?hash = ")sha256-[A-Za-z0-9+/=]+',
            lambda match: match.group(1) + 'sha256-' + 'B' * 43 + '=',
            pins, flags=re.DOTALL,
        )
        cases = (
            ("pins", pins, ["codex"]),
            ("hermes-pin", re.sub(r'github:NousResearch/hermes-agent/v[0-9.]+',
                                   "github:NousResearch/hermes-agent/v2026.10.1", original),
             ["hermes-agent"]),
            ("pins-and-packaging", pins.replace('CARGO_PROFILE_RELEASE_LTO = "false"',
                                                 'CARGO_PROFILE_RELEASE_LTO = "true"'),
             ["omp", "codex", "hermes-agent"]),
            ("pins-and-shared-input", pins.replace('nixos-25.05', 'nixos-unstable'),
             ["omp", "codex", "hermes-agent"]),
        )
        for name, candidate, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                def git(*args: str) -> str:
                    return subprocess.run(
                        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
                    ).stdout.strip()
                git("init", "-q", "-b", "main")
                git("config", "user.name", "Test")
                git("config", "user.email", "test@example.com")
                package = repo / "pkgs/omp/default.nix"
                package.parent.mkdir(parents=True)
                package.write_text("{}")
                flake = repo / "flake.nix"
                flake.write_text(original)
                git("add", ".")
                git("commit", "-q", "-m", "base")
                base = git("rev-parse", "HEAD")
                flake.write_text(candidate)
                git("add", ".")
                git("commit", "-q", "-m", "candidate")
                result = subprocess.run(
                    [str(PACKAGE_DETECTOR), base, "HEAD"], cwd=repo, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                self.assertEqual(json.loads(outputs["packages_json"]), expected)

    def test_camofox_helpers_and_nested_hermes_input_select_their_consumers(self) -> None:
        before_lock = {"root": "root", "nodes": {
            "root": {"inputs": {"hermes-agent": "hermes-agent"}},
            "hermes-agent": {"locked": {"rev": "same"}, "inputs": {"nixpkgs": "hermes-nixpkgs"}},
            "hermes-nixpkgs": {"locked": {"rev": "before"}},
        }}
        after_lock = json.loads(json.dumps(before_lock))
        after_lock["nodes"]["hermes-nixpkgs"]["locked"]["rev"] = "after"
        rust_before = {"root": "root", "nodes": {
            "root": {"inputs": {"codex": "codex", "rust-overlay": "rust-overlay"}},
            "codex": {"locked": {"rev": "same"}, "inputs": {"rust-overlay": ["rust-overlay"]}},
            "rust-overlay": {"locked": {"rev": "before"}},
        }}
        rust_after = json.loads(json.dumps(rust_before))
        rust_after["nodes"]["rust-overlay"]["locked"]["rev"] = "after"
        cases = (
            ("scripts/patch-camofox-browser.py", "before", "after", ["camofox-browser"]),
            ("scripts/repair-camofox-package-lock.py", "before", "after", ["camofox-browser"]),
            ("flake.lock", json.dumps(before_lock), json.dumps(after_lock), ["hermes-agent"]),
            ("flake.lock", json.dumps(rust_before), json.dumps(rust_after), ["codex"]),
            ("scripts/update-github-cli-toolchain", "before", "after", ["github-cli", "supabase-cli"]),
            ("pkgs/github-cli/toolchain.nix", "before", "after", ["github-cli", "supabase-cli"]),
        )
        for path, before, after, expected in cases:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp)
                def git(*args: str) -> str:
                    return subprocess.run(
                        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
                    ).stdout.strip()
                git("init", "-q", "-b", "main")
                git("config", "user.name", "Test")
                git("config", "user.email", "test@example.com")
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(before)
                git("add", ".")
                git("commit", "-q", "-m", "base")
                base = git("rev-parse", "HEAD")
                target.write_text(after)
                git("add", ".")
                git("commit", "-q", "-m", "candidate")
                result = subprocess.run(
                    [str(PACKAGE_DETECTOR), base, "HEAD"], cwd=repo, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                self.assertEqual(json.loads(outputs["packages_json"]), expected)
                self.assertEqual(outputs["run_checks"], "true")

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
    def test_hermes_release_artifact_cannot_change_other_flake_code(self) -> None:
        flake = self.repo / "flake.nix"
        lock_path = self.repo / "flake.lock"
        before = '    hermes-agent.url = "github:NousResearch/hermes-agent/v2026.9.14";\n'
        flake.write_text(before)
        lock = {"version": 7, "root": "root", "nodes": {
            "root": {"inputs": {"hermes-agent": "hermes"}},
            "hermes": {"locked": {"rev": "old"}},
        }}
        lock_path.write_text(json.dumps(lock))
        self.git("add", "flake.nix", "flake.lock")
        self.git("commit", "-qm", "stable Hermes fixture")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        for foreign_edit in (True, False):
            self.reset_candidate()
            flake.write_text(before.replace("v2026.9.14", "v2026.9.24")
                             + ("foreign = true;\n" if foreign_edit else ""))
            lock["nodes"]["hermes"]["locked"]["rev"] = "new"
            lock_path.write_text(json.dumps(lock))
            (self.repo / ".changed-packages").write_text("hermes-agent\n")
            result = self.tool("create", "--phase", "hermes-agent", "--base-sha", self.base,
                               "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact),
                               check=False)
            self.assertEqual(result.returncode == 0, not foreign_edit, result.stderr)
        self.reset_candidate()
        self.tool("verify-apply", "--phase", "hermes-agent", "--base-sha", self.base,
                  "--artifact-dir", str(self.artifact))
        self.assertEqual(flake.read_text(), before.replace("v2026.9.14", "v2026.9.24"))

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

    def test_failed_hermes_update_does_not_suppress_omp_artifact(self) -> None:
        updater = UPDATER.read_text()
        selection = updater.index('case "$update_mode" in', updater.index("block_symphony_ts()"))
        blocks = re.findall(r"^block_([a-z_]+)\(\)", updater, re.MULTILINE)
        stubs = "\n".join(f"block_{name}() {{ echo unexpected-block-{name} >&2; return 1; }}" for name in blocks)
        stubs += '''
lock_fingerprint() { printf 'unchanged'; }
block_flake_update() { printf 'broken\\n' > flake.lock; return 1; }
block_omp() { printf 'updated\\n' > "$omp_package_file"; }
'''
        script = updater[:selection] + stubs + "\n" + updater[selection:]
        for name in re.findall(r'^\w+_(?:package|toolchain)_file="([^"]+)"', updater, re.MULTILINE):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "isolated updater fixture")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        before_lock = (self.repo / "flake.lock").read_text()
        for package, expected_rc in (("hermes-agent", 1), ("omp", 0)):
            result = subprocess.run(
                ["bash", "-c", script, "fixture", "--package", package],
                cwd=self.repo, capture_output=True, text=True,
                env={**os.environ, "GITHUB_OUTPUT": str(self.root / "outputs")},
            )
            self.assertEqual(result.returncode, expected_rc, result.stderr)
            self.assertNotIn("unexpected-block-", result.stderr)
            self.assertEqual((self.repo / "flake.lock").read_text(), before_lock)
        self.assertEqual((self.repo / ".changed-packages").read_text(), "omp\n")
        self.tool("create", "--phase", "omp", "--base-sha", self.base,
                  "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact))
        self.reset_candidate()
        self.tool("verify-apply", "--phase", "omp", "--base-sha", self.base,
                  "--artifact-dir", str(self.artifact))
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.splitlines(),
                         ["pkgs/omp/default.nix"])

    def test_isolated_artifact_rejects_other_package_paths(self) -> None:
        (self.repo / "pkgs/archon/default.nix").write_text("updated\n")
        (self.repo / ".changed-packages").write_text("omp\n")
        result = self.tool("create", "--phase", "omp", "--base-sha", self.base,
                           "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact),
                           check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected paths", result.stderr)

    def test_isolated_hermes_lock_rejects_codex_drift(self) -> None:
        lock = {"version": 7, "root": "root", "nodes": {
            "root": {"inputs": {"hermes-agent": "hermes-agent", "codex": "codex"}},
            "hermes-agent": {"locked": {"rev": "old"}, "inputs": {"nixpkgs": "hermes-nixpkgs"}},
            "hermes-nixpkgs": {"locked": {"rev": "old"}},
            "codex": {"locked": {"rev": "old"}},
        }}
        path = self.repo / "flake.lock"
        path.write_text(json.dumps(lock))
        self.git("add", "flake.lock")
        self.git("commit", "-q", "-m", "lock fixture")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        lock["nodes"]["hermes-nixpkgs"]["locked"]["rev"] = "new"
        lock["nodes"]["codex"]["locked"]["rev"] = "unexpected"
        path.write_text(json.dumps(lock))
        (self.repo / ".changed-packages").write_text("hermes-agent\n")
        result = self.tool("create", "--phase", "hermes-agent", "--base-sha", self.base,
                           "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact),
                           check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside its input closure", result.stderr)
        self.reset_candidate()
        lock["nodes"]["codex"]["locked"]["rev"] = "old"
        path.write_text(json.dumps(lock))
        (self.repo / ".changed-packages").write_text("hermes-agent\n")
        self.tool("create", "--phase", "hermes-agent", "--base-sha", self.base,
                  "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact))
        self.reset_candidate()
        self.tool("verify-apply", "--phase", "hermes-agent", "--base-sha", self.base,
                  "--artifact-dir", str(self.artifact))
        self.assertEqual(json.loads(path.read_text())["nodes"]["codex"]["locked"]["rev"], "old")

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

    def test_remaining_lock_selects_only_hermes(self) -> None:
        (self.repo / "flake.lock").write_text('{"version": 2}\n')
        packages = self.repo / ".changed-packages"
        packages.write_text("hermes-agent\nsupabase-cli\n")
        create_args = (
            "create", "--phase", "remaining", "--base-sha", self.base,
            "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact),
        )
        rejected = self.tool(*create_args, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("metadata does not match patch paths", rejected.stderr)
        packages.write_text("hermes-agent\n")
        self.tool(*create_args)
        self.reset_candidate()
        self.tool(
            "verify-apply", "--phase", "remaining", "--base-sha", self.base,
            "--artifact-dir", str(self.artifact),
        )
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "flake.lock")

    def test_github_cli_artifact_is_a_separate_one_file_transaction(self) -> None:
        package = self.repo / "pkgs/github-cli/default.nix"
        package.parent.mkdir(parents=True)
        package.write_text("version = old;\n")
        self.git("add", "pkgs/github-cli/default.nix")
        self.git("commit", "-q", "-m", "github-cli base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        package.write_text("version = new;\n")
        (self.repo / ".changed-packages").write_text("github-cli\n")
        args = (
            "--base-sha", self.base, "--packages-file", ".changed-packages",
            "--artifact-dir", str(self.artifact),
        )
        rejected = self.tool("create", "--phase", "remaining", *args, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("outside its publication lane", rejected.stderr)
        (self.repo / "flake.lock").write_text('{"version": 2}\n')
        rejected = self.tool("create", "--phase", "github-cli", *args, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unexpected paths", rejected.stderr)
        (self.repo / "flake.lock").write_text('{"version": 1}\n')
        self.tool("create", "--phase", "github-cli", *args)
        self.reset_candidate()
        self.tool(
            "verify-apply", "--phase", "github-cli", "--base-sha", self.base,
            "--artifact-dir", str(self.artifact),
        )
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "pkgs/github-cli/default.nix")

    def test_github_cli_toolchain_only_artifact_round_trip(self) -> None:
        pin = self.repo / "pkgs/github-cli/toolchain.json"
        pin.parent.mkdir(parents=True)
        pin.write_text('{"go_version":"1.27.1"}\n')
        self.git("add", "pkgs/github-cli/toolchain.json")
        self.git("commit", "-q", "-m", "toolchain base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        pin.write_text('{"go_version":"1.28.1"}\n')
        (self.repo / ".changed-packages").write_text("github-cli\n")
        args = ("--base-sha", self.base, "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact))
        rejected = self.tool("create", "--phase", "remaining", *args, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("outside its publication lane", rejected.stderr)
        self.tool("create", "--phase", "github-cli", *args)
        self.reset_candidate()
        self.tool("verify-apply", "--phase", "github-cli", "--base-sha", self.base, "--artifact-dir", str(self.artifact))
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "pkgs/github-cli/toolchain.json")

    def test_camofox_artifact_cannot_include_or_block_remaining_packages(self) -> None:
        package = self.repo / "pkgs/camofox-browser/default.nix"
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text("version = old;\n")
        self.git("add", "pkgs/camofox-browser/default.nix")
        self.git("commit", "-q", "-m", "browser base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        package.write_text("version = new;\n")
        (self.repo / ".changed-packages").write_text("camofox-browser\n")
        args = ("--base-sha", self.base, "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact))
        rejected = self.tool("create", "--phase", "remaining", *args, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        other = self.repo / "pkgs/archon/default.nix"
        original = other.read_text()
        other.write_text("version = new;\n")
        rejected = self.tool("create", "--phase", "camofox", *args, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        other.write_text(original)
        self.tool("create", "--phase", "camofox", *args)
        self.reset_candidate()
        self.tool("verify-apply", "--phase", "camofox", "--base-sha", self.base, "--artifact-dir", str(self.artifact))
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "pkgs/camofox-browser/default.nix")

    def test_supabase_toolchain_only_artifact_round_trip(self) -> None:
        pin = self.repo / "pkgs/supabase-cli/toolchain.json"
        pin.parent.mkdir(parents=True, exist_ok=True)
        pin.write_text('{"go_version":"1.26.5"}\n')
        self.git("add", "pkgs/supabase-cli/toolchain.json")
        self.git("commit", "-q", "-m", "supabase toolchain base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        pin.write_text('{"go_version":"1.27.1"}\n')
        (self.repo / ".changed-packages").write_text("supabase-cli\n")
        self.tool("create", "--phase", "remaining", "--base-sha", self.base,
            "--packages-file", ".changed-packages", "--artifact-dir", str(self.artifact))
        self.reset_candidate()
        self.tool("verify-apply", "--phase", "remaining", "--base-sha", self.base,
            "--artifact-dir", str(self.artifact))
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "pkgs/supabase-cli/toolchain.json")

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
        (self.repo / ".changed-packages").write_text("hermes-agent\n")
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

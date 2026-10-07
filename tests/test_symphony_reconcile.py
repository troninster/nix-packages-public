import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BASE = "b10fc974f23f479e3334ea1526833c4d55745876"
SPEC = importlib.util.spec_from_file_location("symphony_reconcile", ROOT / "tools/component-reconcile.py")
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)
reconcile.configure("symphony-ts")
adapter, release = reconcile.source_adapter, reconcile.release


class SymphonyReconcileTests(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_SHA": "a" * 40, "GITHUB_WORKFLOW_REF": reconcile.source_engine.WORKFLOW})
        environment.start()
        self.addCleanup(environment.stop)

    def test_prepared_pins_reapply_on_unrelated_main_advance_without_candidate_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            fresh = Path(temporary)
            for path in release.FILES:
                destination = fresh / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / path).read_bytes())
            baseline = {"revision": "e" * 40, "componentSha256": reconcile.source_engine.component_digest(fresh)}
            original = (fresh / adapter.RECIPE).read_text()
            selected = {**adapter.pins(original), "rev": "f" * 40}
            updated = adapter.apply_pins(original, selected)
            prepared_digest = release.sha({path: hashlib.sha256(updated.encode() if path == adapter.RECIPE
                else (fresh / path).read_bytes()).hexdigest() for path in release.FILES})
            plan = {"schema": 1, "mode": "PREPARED", "baselineComponentSha256": baseline["componentSha256"],
                "componentSha256": prepared_digest, "pins": selected, "package": {}, "proof": reconcile.source_engine.receipt()}

            def git(args):
                self.assertEqual(args[0], "git")
                output = "f" * 40 if "rev-parse" in args else adapter.RECIPE if "--name-only" in args else ""
                return mock.Mock(stdout=output.encode())

            with mock.patch.object(release, "source_identity", return_value=baseline), \
                    mock.patch.object(release, "run", side_effect=git) as commands:
                self.assertEqual(reconcile.publish_source(ROOT, plan, fresh), ("f" * 40, None))
                self.assertEqual((fresh / adapter.RECIPE).read_text(), updated)
                pushes = [call.args[0] for call in commands.call_args_list if "push" in call.args[0]]
                self.assertEqual(len(pushes), 1)
                self.assertIn("HEAD:refs/heads/main", pushes[0])
                self.assertNotIn("--force", pushes[0])
            baseline["componentSha256"] = "0" * 64
            with mock.patch.object(release, "source_identity", return_value=baseline), \
                    mock.patch.object(release, "run", side_effect=git) as commands:
                with self.assertRaises(RuntimeError):
                    reconcile.publish_source(ROOT, plan, fresh)
                self.assertFalse(any("push" in call.args[0] for call in commands.call_args_list))

    def test_durable_ready_dedup_uses_component_and_package_not_common_main_sha(self):
        source = {"componentSha256": "a" * 64, "revision": "e" * 40}
        package = {"storePath": "fixture", "drvPath": "fixture.drv", "version": "0.1.8"}
        record = {"source": {**source, "revision": "f" * 40}, "package": package, "releaseId": "b" * 64}
        entries = {
            "git/ref/heads/component-releases": {"object": {"sha": "catalog"}},
            "git/commits/catalog": {"tree": {"sha": "tree"}},
            "git/trees/tree?recursive=1": {"tree": [{"type": "blob", "path": release.RECORDS + "/" + "b" * 64 + ".json", "sha": "blob"}]},
            "git/blobs/blob": {"encoding": "base64", "size": 200,
                "content": base64.b64encode(release.canonical(record)).decode()},
        }
        with mock.patch.object(release, "api", side_effect=lambda path, **kw: entries[path]), \
                mock.patch.object(release, "validate_record", side_effect=lambda value, stage: value):
            self.assertTrue(reconcile.has_ready(source, package))
            self.assertFalse(reconcile.has_ready(source, {**package, "drvPath": "different.drv"}))
        with mock.patch.object(reconcile, "publish_source", return_value=(source["revision"], source)), \
                mock.patch.object(reconcile, "has_ready", return_value=True), \
                mock.patch.object(reconcile, "wake_factory") as factory:
            self.assertEqual(reconcile.reconcile(ROOT, {"package": package}, ROOT), "component-already-READY")
            factory.assert_not_called()

    def test_pending_factory_and_six_hour_retry_cooldown_do_not_storm_dispatches(self):
        run = {"repository": {"full_name": release.REPO}, "head_repository": {"full_name": release.REPO},
            "head_branch": "main", "event": "workflow_dispatch", "status": "in_progress",
            "display_title": "component:symphony-ts",
            "created_at": datetime.now(timezone.utc).isoformat()}

        def receipts(endpoint):
            if f"&status={run['status']}&" in endpoint:
                # Completed lookup is a six-hour window, not lifetime history.
                if run["status"] == "completed":
                    self.assertIn("&created=", endpoint)
                return {"total_count": 1, "workflow_runs": [run]}
            return {"total_count": 0, "workflow_runs": []}

        with mock.patch.object(release, "api", side_effect=receipts), \
                mock.patch.object(release, "run") as dispatch, mock.patch.object(release, "approved_ancestor"):
            self.assertEqual(reconcile.wake_factory("e" * 40), "factory-pending")
            run["status"] = "completed"
            self.assertEqual(reconcile.wake_factory("e" * 40), "factory-retry-cooldown")
            dispatch.assert_not_called()
            self.assertEqual(reconcile.wake_factory("e" * 40, new_source=True), "factory-dispatched")
            self.assertEqual(json.loads(dispatch.call_args.kwargs["data"]),
                             {"ref": "main", "inputs": {"component": "symphony-ts", "source_revision": "e" * 40}})
        with mock.patch.object(release, "api", return_value={"total_count": 2, "workflow_runs": [run]}), \
                mock.patch.object(release, "run") as dispatch:
            with self.assertRaisesRegex(RuntimeError, "Incomplete active factory"):
                reconcile.wake_factory("e" * 40)
            dispatch.assert_not_called()

    def test_handoff_routes_only_symphony_but_unknown_shared_updater_edits_keep_full_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)

            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True,
                    capture_output=True, text=True).stdout.strip()

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.com")
            for path in ROOT.glob("pkgs/*/default.nix"):
                destination = repo / path.relative_to(ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("{}")
            shared = ("scripts/update-upstream-inputs", ".github/workflows/ci.yml")
            for path in shared:
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(subprocess.run(["git", "show", f"{BASE}:{path}"], cwd=ROOT,
                    check=True, capture_output=True).stdout)
            git("add", ".")
            git("commit", "-qm", "baseline")
            baseline = git("rev-parse", "HEAD")
            for path in (*shared, "tools/symphony-source.py", "tools/symphony-reconcile.py",
                         "tests/test_symphony_source.py", "tests/test_symphony_reconcile.py",
                         ".github/workflows/symphony-source.yml", "scripts/detect-ci-packages"):
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / path).read_bytes())
            git("add", ".")
            git("commit", "-qm", "handoff")

            def routed():
                result = subprocess.run([str(ROOT / "scripts/detect-ci-packages"), baseline, "HEAD"],
                    cwd=repo, check=True, capture_output=True, text=True)
                rows = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                self.assertEqual(rows["run_checks"], "true")
                return json.loads(rows["packages_json"])

            self.assertEqual(routed(), ["symphony-ts"])
            with (repo / shared[0]).open("a") as stream:
                stream.write("\n# unknown shared updater edit\n")
            git("add", ".")
            git("commit", "-qm", "unknown")
            self.assertEqual(len(routed()), 13)
            self.assertIn("codex", routed())
            self.assertIn("notion-cli", routed())

    def test_default_off_workflow_separates_candidate_and_write_authority(self):
        workflow = (ROOT / ".github/workflows/symphony-source.yml").read_text()
        self.assertIn("vars.COMPONENT_SOURCE_UPDATES == '1'", workflow)
        self.assertIn("27 */6 * * *", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("uses: ./.github/workflows/component-source-worker.yml", workflow)
        worker = (ROOT / ".github/workflows/component-source-worker.yml").read_text()
        self.assertEqual(worker.count("ref: ${{ github.workflow_sha }}"), 2)
        prepare, publish = worker.split("  prepare:\n", 1)[1].split("  publish:\n", 1)
        self.assertIn("needs: prepare", publish)
        self.assertNotIn("contents: write", prepare)
        self.assertNotIn("actions: write", prepare)
        self.assertIn("contents: write", publish)
        self.assertIn("actions: write", publish)
        self.assertNotIn("Install Nix", publish)
        self.assertNotIn("secrets.", workflow + worker)
        self.assertNotIn("secrets: inherit", workflow + worker)
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn('run: bash ./scripts/build-component "${{ matrix.package }}"', ci)
        component_step = ci.split("      - name: Verify the enrolled independent component without cache publication\n", 1)[1].split("      - name: Build package\n", 1)[0]
        self.assertIn("id: component", ci.split("  build:", 1)[1])
        self.assertNotIn("id: component", ci.split("  build:", 1)[0])
        self.assertIn("CACHIX_AUTH_TOKEN: ''", component_step)
        self.assertIn("REQUIRE_CACHIX_PUSH: '0'", component_step)
        self.assertIn("inputs.source_revision == '' && steps.component.outputs.enrolled != 'true'", ci)
        self.assertIn('"$RUNNER_TEMP/repair-build-package" "$PACKAGE"', ci)


if __name__ == "__main__":
    unittest.main()

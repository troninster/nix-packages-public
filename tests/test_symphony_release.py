import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("symphony_release", ROOT / "tools/symphony-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
RUNTIME = "/nix/store/" + "0" * 32 + "-symphony-ts-0.1.8"
DEPENDENCY = "/nix/store/" + "1" * 32 + "-nodejs-22.0.0"
HASH = "sha256-" + base64.b64encode(b"x" * 32).decode()


def record(stage="SIGNED"):
    value = {"schema": 1, "status": stage, "component": "symphony-ts", "platform": "x86_64-linux",
             "source": {"repository": release.REPO, "revision": "a" * 40, "directory": release.DIRECTORY,
                        "componentSha256": "b" * 64, "lockSha256": "c" * 64, "upstreamRevision": "d" * 40},
             "package": {"version": "0.1.8", "drvPath": RUNTIME + ".drv", "storePath": RUNTIME},
             "closure": {RUNTIME: {"narHash": HASH, "narSize": 10, "references": [DEPENDENCY]},
                         DEPENDENCY: {"narHash": HASH, "narSize": 10, "references": []}},
             "verification": {"policy": "symphony-repeat-cold-v1", "repeatBuild": True,
                              "coldImport": stage == "READY", "smoke": True}}
    value["releaseId"] = release.sha(release.identity(value))
    return value


def bundle(directory, stage="SIGNED"):
    directory.mkdir()
    value = record(stage)
    cache = directory.parent / "fixture-cache"
    cache.mkdir()
    (cache / "nix-cache-info").write_text("StoreDir: /nix/store\n")
    for index, (path, info) in enumerate(value["closure"].items()):
        nar = "nar/" + str(index) * 52 + ".nar.zst"
        (cache / "nar").mkdir(exist_ok=True)
        (cache / nar).write_bytes(b"fixture nar")
        text = (f"StorePath: {path}\nURL: {nar}\nCompression: zstd\nNarHash: {HASH}\n"
                f"NarSize: 10\nReferences: {' '.join(Path(ref).name for ref in info['references'])}\n"
                f"Sig: {release.PUBLIC_KEY.split(':', 1)[0]}:fixture\n")
        (cache / (Path(path).name[:32] + ".narinfo")).write_text(text)
    archive_hash, parts = release.archive_cache(cache, directory)
    delivery = {"schema": 1, "component": "symphony-ts", "platform": "x86_64-linux",
                "releaseId": value["releaseId"], "storePath": RUNTIME,
                "archiveSha256": archive_hash, "parts": parts, "closure": value["closure"]}
    for name, body in (("record.json", value), ("release.json", delivery),
                       ("proof.json", release.proof(value, delivery, stage))):
        release.write_json(directory / name, body)
    return value, delivery


class SymphonyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                                                       "GITHUB_WORKFLOW_REF": release.WORKFLOW,
                                                       "GITHUB_WORKFLOW_SHA": "f" * 40})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_record_identity_is_stable_across_proof_stages_and_incomplete_is_not_ready(self):
        built, signed, ready = record("BUILT"), record("SIGNED"), record("READY")
        self.assertEqual(built["releaseId"], signed["releaseId"])
        self.assertEqual(signed["releaseId"], ready["releaseId"])
        release.validate_record(ready, "READY")
        with self.assertRaises(RuntimeError):
            release.validate_record(signed, "READY")
        ready["closure"][RUNTIME]["references"] = []
        with self.assertRaises(RuntimeError):
            release.validate_record(ready, "READY")

    def test_runtime_archive_roundtrip_and_reference_mismatch_fail_before_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            value, delivery = bundle(fixture / "release")
            self.assertEqual(release.load_bundle(fixture / "release", "SIGNED")[0], value)
            self.assertEqual(delivery["archiveSha256"], delivery["parts"][0]["sha256"])
            with mock.patch.object(release, "run", return_value=mock.Mock(stdout=HASH.encode())):
                cache = release.unpack(fixture / "release", delivery, fixture / "unpacked", True)
                self.assertTrue(cache.startswith("file:"))
                delivery["closure"][RUNTIME]["references"] = []
                with self.assertRaises(RuntimeError):
                    release.unpack(fixture / "release", delivery, fixture / "mismatch", True)

    def test_import_disables_builders_and_checks_full_graph_and_approved_signatures(self):
        value = record()
        delivery = {"storePath": RUNTIME}
        with mock.patch.object(release, "unpack", return_value="file:///fixture"), \
                mock.patch.object(release, "closure_info", return_value=value["closure"]), \
                mock.patch.object(release, "run", return_value=mock.Mock()) as commands:
            release.import_runtime(Path("/fixture"), value, delivery, "local?root=/cold", True)
            self.assertEqual(commands.call_count, 3)
            for call in commands.call_args_list:
                args = call.args[0]
                self.assertIn("--max-jobs", args)
                self.assertEqual(args[args.index("--max-jobs") + 1], "0")
                self.assertEqual(args[args.index("builders") + 1], "")
                self.assertEqual(args[args.index("require-sigs") + 1], "true")
                self.assertIn(release.PUBLIC_KEY, args)
                self.assertNotIn("--no-check-sigs", args)
            self.assertEqual(commands.call_args_list[1].args[0][0:2], ["nix", "copy"])

    def test_cold_ready_requires_successful_store_mount_and_network_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            bundle(fixture / "signed")
            with mock.patch.object(release, "import_runtime"), \
                    mock.patch.object(release, "run", side_effect=RuntimeError("smoke failed")):
                with self.assertRaises(RuntimeError):
                    release.cold(fixture / "signed", fixture / "cold", fixture / "failed-ready")
            self.assertFalse((fixture / "failed-ready").exists())
            with mock.patch.object(release, "import_runtime"), \
                    mock.patch.object(release, "run", return_value=mock.Mock(stdout=b"symphony usage")) as command:
                release.cold(fixture / "signed", fixture / "cold", fixture / "ready")
                args = command.call_args.args[0]
                self.assertIn("--mount", args)
                self.assertIn("--net", args)
                self.assertIn('mount --bind "$1/nix/store" /nix/store', args[args.index("-c") + 1])
            ready, _ = release.load_bundle(fixture / "ready", "READY")
            self.assertTrue(ready["verification"]["coldImport"])

    def test_signing_key_file_is_private_removed_and_never_used_for_candidate_execution(self):
        value = record("BUILT")
        filename = None

        def signed(args):
            nonlocal filename
            self.assertEqual(args[:3], ["nix", "store", "sign"])
            filename = Path(args[args.index("--key-file") + 1])
            self.assertEqual(filename.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(release.SIGNING_ENV, os.environ)
            return mock.Mock()

        with mock.patch.object(release, "load_bundle", return_value=(value, {})), \
                mock.patch.object(release, "closure_info", return_value=value["closure"]), \
                mock.patch.object(release, "run", side_effect=signed), \
                mock.patch.dict(os.environ, {release.SIGNING_ENV: release.PUBLIC_KEY.split(":")[0] + ":fixture-key"}):
            release.sign(Path("/fixture"), Path("/sign-store"))
        self.assertFalse(filename.exists())

    def test_existing_release_digest_conflict_is_preserved_without_upload_or_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            value, delivery = bundle(fixture / "ready", "READY")
            paths = [fixture / "ready/release.json", fixture / "ready/runtime.tar.part000"]
            existing = {"draft": False, "assets": [{"name": path.name, "state": "uploaded",
                         "size": path.stat().st_size, "digest": "sha256:" + release.digest(path)} for path in paths]}
            existing["assets"][0]["digest"] = "sha256:" + "0" * 64
            with mock.patch.object(release, "api", return_value=existing), mock.patch.object(release, "run") as mutation:
                with self.assertRaises(RuntimeError):
                    release.release_assets(fixture / "ready", value, delivery)
                mutation.assert_not_called()

    def test_main_advancement_is_allowed_but_foreign_source_and_catalog_races_are_not(self):
        workflow = (ROOT / ".github/workflows/symphony-release.yml").read_text()
        self.assertEqual(workflow.count("ref: ${{ github.workflow_sha }}"), 4)
        self.assertNotIn("ref: main", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("  pull_request:\n", workflow)
        self.assertIn("run: python3 -m unittest discover -s tests -p test_symphony_release.py -v", workflow)
        self.assertEqual(release.MAX_PARTS, 16)
        with mock.patch.object(release, "api", return_value={"status": "ahead", "merge_base_commit": {"sha": "a" * 40}}):
            release.approved_ancestor("a" * 40)
        with mock.patch.object(release, "api", return_value={"status": "ahead", "merge_base_commit": {"sha": "b" * 40}}):
            with self.assertRaises(RuntimeError):
                release.approved_ancestor("a" * 40)
        with mock.patch.object(release, "load_bundle", return_value=(record("READY"), {})), \
                mock.patch.object(release, "approved_ancestor"), \
                mock.patch.object(release, "catalog_snapshot", return_value=("a" * 40, "tree", True, None)), \
                mock.patch.object(release, "release_assets"), \
                mock.patch.object(release, "api", side_effect=lambda path, *a, **kw: {"object": {"sha": "b" * 40}} if path.startswith("git/ref/") else {"sha": "fixture"}) as api:
            with self.assertRaises(RuntimeError):
                release.publish(Path("/fixture"))
            self.assertFalse(any(call.args[0].startswith("git/refs") for call in api.call_args_list))


if __name__ == "__main__":
    unittest.main()

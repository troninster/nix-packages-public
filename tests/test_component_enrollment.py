"""One synthetic enrollment exercises shared engines; no second production authority."""
import base64
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


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def synthetic(root):
    descriptor = json.loads((ROOT / "components/symphony-ts.json").read_text())
    descriptor.update(component="sample", directory="pkgs/sample", packageAttr="sample", packageName="sample",
                      verificationPolicy="sample-repeat-cold-v1", sourceAdapter="pkgs/sample/update-source.py",
                      sourceFiles=["pkgs/sample/" + name for name in ("default.nix", "flake.nix", "flake.lock")],
                      smoke={"argv": ["bin/sample", "--help"], "contains": "sample", "absentPaths": []})
    (root / "components").mkdir()
    (root / "components/sample.json").write_text(json.dumps(descriptor))
    package = root / "pkgs/sample"
    package.mkdir(parents=True)
    (package / "default.nix").write_text('rev = "' + "e" * 40 + '";\n')
    (package / "flake.nix").write_text("{}")
    lock = json.loads((ROOT / "pkgs/symphony-ts/flake.lock").read_text())
    lock["nodes"]["root"]["inputs"]["extra"] = "extra"
    lock["nodes"]["extra"] = dict(lock["nodes"]["nixpkgs"])
    (package / "flake.lock").write_text(json.dumps(lock))
    (package / "update-source.py").write_text('''RECIPE = "pkgs/sample/default.nix"
def upstream_revision(files):
    return files[RECIPE].decode().split('"')[1]
def apply_pins(text, pins):
    return text.replace(text.split('"')[1], pins["rev"])
def select(source):
    pins = {"rev": "f" * 40}
    recipe = source / RECIPE
    recipe.write_text(apply_pins(recipe.read_text(), pins))
    return {"mode": "PREPARED", "pins": pins}
''')
    return descriptor, lock


class ComponentEnrollmentTests(unittest.TestCase):
    def test_historical_c77_record_identity_is_verbatim_and_only_symphony_is_enrolled(self):
        release = module("component-release")
        release.configure("symphony-ts")
        record = json.loads((ROOT / "tests/fixtures/symphony-c77-ready.json").read_bytes())
        self.assertEqual(release.contract.enrolled(), ["symphony-ts"])
        release.validate_record(record, "READY")
        self.assertEqual(release.sha(record), "e8b0d4c4a9240965ed7a1269a6a0f396bb1a2df61342e2f8f6fd071c2847e7ab")
        self.assertEqual(record["releaseId"], "efe43bf41351b0c532e8a6e4095e29a5f7a83be80f76a1d27f4c878a7180a021")
        for path in release.FILES:
            original = subprocess.check_output(["git", "show", "c77d2a192f957a6ba1e65b242a6d9cfe30418d5f:" + path], cwd=ROOT)
            self.assertEqual((ROOT / path).read_bytes(), original)

    def test_second_descriptor_owns_extra_locked_graph_build_cold_and_catalog_without_core_edits(self):
        release, builder = module("component-release"), module("component-build")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, lock = synthetic(root)
            release.configure("sample", root)
            release.contract.locked_graph(lock)
            for invalid in ("missing", "cycle", "unlocked"):
                bad = json.loads(json.dumps(lock))
                if invalid == "missing":
                    bad["nodes"]["root"]["inputs"]["extra"] = "missing"
                elif invalid == "cycle":
                    bad["nodes"]["extra"]["inputs"] = {"again": "extra"}
                else:
                    del bad["nodes"]["extra"]["locked"]
                with self.assertRaises(RuntimeError):
                    release.contract.locked_graph(bad)
            runtime = "/nix/store/" + "0" * 32 + "-sample-1.0.0"
            narhash = "sha256-" + base64.b64encode(b"x" * 32).decode()
            record = {"schema": 1, "status": "SIGNED", "component": "sample", "platform": descriptor["platform"],
                      "source": {"repository": release.REPO, "revision": "a" * 40, "directory": release.DIRECTORY,
                                 "componentSha256": "b" * 64, "lockSha256": "c" * 64, "upstreamRevision": "e" * 40},
                      "package": {"version": "1.0.0", "drvPath": runtime + ".drv", "storePath": runtime},
                      "closure": {runtime: {"narHash": narhash, "narSize": 10, "references": []}},
                      "verification": {"policy": descriptor["verificationPolicy"], "repeatBuild": True,
                                       "coldImport": False, "smoke": True}}
            record["releaseId"] = release.sha(release.identity(record))
            release.validate_record(record, "SIGNED")
            with mock.patch.object(builder.subprocess, "run", side_effect=[mock.Mock(), mock.Mock(stdout=(runtime + "\n").encode()),
                                   mock.Mock(), mock.Mock(stdout=b"sample usage")]) as commands:
                self.assertEqual(builder.build(descriptor, root), runtime)
                self.assertIn("./pkgs/sample#sample", commands.call_args_list[1].args[0])
                self.assertIn("--rebuild", commands.call_args_list[2].args[0])
                self.assertEqual(commands.call_args_list[3].args[0], [runtime + "/bin/sample", "--help"])
            signed = root / "signed"
            signed.mkdir()
            (signed / "runtime.tar.part000").write_bytes(b"unchanged signed runtime fixture")
            part = {"name": "runtime.tar.part000", "size": (signed / "runtime.tar.part000").stat().st_size,
                    "sha256": release.digest(signed / "runtime.tar.part000")}
            delivery = {"schema": 1, "component": "sample", "platform": descriptor["platform"],
                        "releaseId": record["releaseId"], "storePath": runtime,
                        "archiveSha256": part["sha256"], "parts": [part], "closure": record["closure"]}
            environment = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                           "GITHUB_WORKFLOW_SHA": "a" * 40, "GITHUB_WORKFLOW_REF": release.WORKFLOW}
            with mock.patch.dict(os.environ, environment):
                for name, value in (("record.json", record), ("release.json", delivery),
                                    ("proof.json", release.proof(record, delivery, "SIGNED"))):
                    release.write_json(signed / name, value)
                with mock.patch.object(release, "import_runtime"), mock.patch.object(release, "run", return_value=mock.Mock(stdout=b"sample usage")) as commands:
                    ready = root / "ready"
                    release.cold(signed, root / "cold-store", ready)
                    self.assertIn("--net", commands.call_args.args[0])
                    self.assertIn(runtime + "/bin/sample", commands.call_args.args[0])
                self.assertEqual((ready / part["name"]).read_bytes(), (signed / part["name"]).read_bytes())
                ready_record, ready_delivery = release.load_bundle(ready, "READY")
                draft = {"id": 1, "target_commitish": "a" * 40, "draft": True,
                         "assets": [{"name": path.name, "state": "uploaded", "size": path.stat().st_size,
                                     "digest": "sha256:" + release.digest(path)}
                                    for path in (ready / "release.json", ready / part["name"])]}
                with mock.patch.object(release, "find_release", return_value=draft), mock.patch.object(release, "api") as api:
                    tag = release.release_assets(ready, ready_record, ready_delivery)
                    self.assertEqual(tag, "sample-" + descriptor["platform"] + "-" + record["releaseId"])
                    self.assertEqual(release.RECORDS, "releases/sample/x86_64-linux")
                    api.assert_called_once_with("releases/1", "PATCH", {"draft": False, "prerelease": True, "make_latest": "false"})

    def test_second_component_source_gate_is_common_and_foreign_factory_does_not_suppress_it(self):
        engine = module("component-source")
        reconcile = module("component-reconcile")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            synthetic(root)
            engine.configure("sample", root)
            with (mock.patch.object(engine.release, "run", return_value=mock.Mock(stdout=b"")),
                    mock.patch.object(engine.release, "source_identity", return_value={"componentSha256": "a" * 64}),
                    mock.patch.object(engine, "candidate_run", side_effect=RuntimeError("repeat failed")) as execution,
                    mock.patch.object(engine, "plan") as plan):
                with self.assertRaisesRegex(RuntimeError, "repeat failed"):
                    engine.prepare(root)
                self.assertEqual(execution.call_args.args[0][-1], "sample")
                plan.assert_not_called()
            reconcile.configure("sample", root)
            foreign = {"repository": {"full_name": reconcile.release.REPO}, "head_repository": {"full_name": reconcile.release.REPO},
                       "head_branch": "main", "event": "workflow_dispatch", "display_title": "component:symphony-ts", "status": "in_progress"}
            def native(endpoint):
                return {"total_count": 1, "workflow_runs": [foreign]} if "&status=in_progress&" in endpoint else {"total_count": 0, "workflow_runs": []}
            with (mock.patch.object(reconcile.release, "api", side_effect=native), mock.patch.object(reconcile.release, "approved_ancestor"),
                    mock.patch.object(reconcile.release, "run") as dispatch):
                self.assertEqual(reconcile.wake_factory("a" * 40), "factory-dispatched")
                self.assertEqual(json.loads(dispatch.call_args.kwargs["data"])["inputs"]["component"], "sample")


if __name__ == "__main__":
    unittest.main()

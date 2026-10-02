import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hermes_release", ROOT / "tools/hermes-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class HermesReleaseTests(unittest.TestCase):
    def test_single_part_export_reuses_archive_and_keeps_matching_digest(self):
        output = "/nix/store/" + "0" * 32 + "-hermes-agent-0.21.5"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "release"
            def command(args):
                if args[:2] == ["nix", "path-info"]:
                    return mock.Mock(stdout=json.dumps({output: {"narHash": "fixture", "narSize": 10}}))
                if args[:2] == ["nix", "copy"]:
                    (directory / "cache").mkdir()
                    (directory / "cache/nix-cache-info").write_text("StoreDir: /nix/store\n")
                return mock.Mock(stdout="a" * 40)
            with mock.patch.object(release, "run", side_effect=command), \
                    mock.patch.object(release, "add_signatures"):
                release.export(output, directory)
            manifest = json.loads((directory / "manifest.json").read_text())
            self.assertEqual(len(manifest["parts"]), 1)
            self.assertEqual(manifest["archiveSha256"], manifest["parts"][0]["sha256"])
            self.assertEqual(release.digest(directory / manifest["parts"][0]["name"]), manifest["archiveSha256"])
            self.assertFalse((directory / "runtime.tar").exists())
            self.assertFalse((directory / "cache").exists())

    def test_core_selection_accepts_host_and_ci_nix_json(self):
        name = "0" * 32 + "-hermes-agent-0.21.5.drv"
        core = {"env": {"HERMES_NIX_BUILD": "1"}}
        self.assertEqual(release.core_derivation({"/nix/store/" + name: core}), "/nix/store/" + name)
        self.assertEqual(release.core_derivation({"version": 4, "derivations": {name: core}}),
                         "/nix/store/" + name)
        with self.assertRaises(RuntimeError):
            release.core_derivation({"version": 4, "derivations": {name: core, name + "-duplicate": core}})

    def test_tag_is_output_specific_and_rejects_other_packages(self):
        output = "/nix/store/" + "0" * 32 + "-hermes-agent-0.21.5"
        self.assertEqual(release.tag(output), "hermes-agent-x86_64-linux-" + "0" * 32)
        with self.assertRaises(RuntimeError):
            release.tag(output.replace("hermes-agent", "neurobooks"))

    def test_remote_signature_cannot_cover_different_runtime_bytes(self):
        output = "/nix/store/" + "0" * 32 + "-hermes-agent-0.21.5"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            local = f"StorePath: {output}\nNarHash: sha256:original\nNarSize: 10\nReferences: \n"
            (cache / ("0" * 32 + ".narinfo")).write_text(local)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = local.replace("original", "different").encode()
            with mock.patch.object(release, "urlopen", return_value=response), self.assertRaises(RuntimeError):
                release.add_signatures(cache, output)

    def test_publisher_is_required_before_upstream_commit_and_ci_acceptance(self):
        workflow = (ROOT / ".github/workflows/update-package.yml").read_text()
        self.assertLess(workflow.index("Publish Hermes before advancing its source pin"),
                        workflow.index("Commit and push selected update"))
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("needs.publish-hermes.result", ci)
        self.assertLess(ci.index("Check package reproducibility"),
                        ci.index("Upload verified Hermes release"))
        delivery = (ROOT / "scripts/build-package").read_text()
        self.assertIn('if [[ "$package" == hermes-agent ]]', delivery)
        self.assertNotIn("cachix pin", delivery)
        self.assertLess(delivery.index('"$core_drv^out"'), delivery.index("cachix push"))

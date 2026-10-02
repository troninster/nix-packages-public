import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hermes_release", ROOT / "tools/hermes-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class HermesReleaseTests(unittest.TestCase):
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

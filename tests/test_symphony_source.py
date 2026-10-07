import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("symphony_source", ROOT / "pkgs/symphony-ts/update-source.py")
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


class SymphonySourceTests(unittest.TestCase):
    def test_only_four_literal_pins_change_and_unknown_recipe_shape_fails(self):
        original = (ROOT / source.RECIPE).read_text()
        selected = {**source.pins(original), "rev": "f" * 40, "version": "0.1.9", "pnpmHash": source.FAKE_HASH}
        changed = source.apply_pins(original, selected)
        self.assertEqual(source.pins(changed), selected)
        self.assertEqual(source.apply_pins(changed, source.pins(original)), original)
        with self.assertRaises(RuntimeError):
            source.apply_pins(original.replace("pnpm_10.fetchDeps", "newFetcher"), selected)

    def test_pnpm_probe_accepts_only_its_exact_known_fixed_output_mismatch(self):
        derivation = "/nix/store/" + "0" * 32 + "-symphony-ts-pnpm-deps.drv"
        text = f"error: hash mismatch in fixed-output derivation '{derivation}':\n specified: {source.FAKE_HASH}\n got: {source.FAKE_HASH}\n"
        result = mock.Mock(returncode=1, stderr=text.encode(), stdout=b"")
        self.assertEqual(source.dependency_hash(result, derivation), source.FAKE_HASH)
        result.stderr = text.replace(derivation, derivation.replace("pnpm", "foreign")).encode()
        with self.assertRaises(RuntimeError):
            source.dependency_hash(result, derivation)
        result.stderr = b"ERR_PNPM_UNKNOWN_DEPENDENCY: unsupported dependency"
        with self.assertRaises(RuntimeError):
            source.dependency_hash(result, derivation)

    def test_unchanged_upstream_does_not_probe_or_build_and_candidate_has_no_api_tokens(self):
        with mock.patch.object(source, "upstream", return_value={"sha": source.pins((ROOT / source.RECIPE).read_text())["rev"]}), \
                mock.patch.object(source, "candidate_run") as candidate:
            self.assertEqual(source.select(ROOT)["mode"], "CURRENT")
            candidate.assert_not_called()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "fixture", "GITHUB_TOKEN": "fixture",
                                         source.release.SIGNING_ENV: "fixture", "CACHIX_AUTH_TOKEN": "fixture"}), \
                mock.patch.object(source.subprocess, "run", return_value=mock.Mock(returncode=0)) as command:
            source.candidate_run(["nix", "fixture"], ROOT)
            self.assertFalse({"GH_TOKEN", "GITHUB_TOKEN", "CACHIX_AUTH_TOKEN", source.release.SIGNING_ENV}
                             & set(command.call_args.kwargs["env"]))

    def test_changed_candidate_must_pass_common_repeat_gate_before_preparation_is_emitted(self):
        spec = importlib.util.spec_from_file_location("component_source_test", ROOT / "tools/component-source.py")
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        engine.configure("symphony-ts")
        with mock.patch.object(engine.release, "run", return_value=mock.Mock(stdout=b"")), \
                mock.patch.object(engine.release, "source_identity", return_value={"componentSha256": "a" * 64}), \
                mock.patch.object(engine.source_adapter, "select", return_value={"mode": "PREPARED", "pins": {}}), \
                mock.patch.object(engine, "candidate_run", side_effect=RuntimeError("repeat failed")) as candidate, \
                mock.patch.object(engine, "plan") as plan:
            with self.assertRaisesRegex(RuntimeError, "repeat failed"):
                engine.prepare(ROOT)
            self.assertEqual(candidate.call_args.args[0], ["bash", str(ROOT / "scripts/build-component"), "symphony-ts"])
            plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()

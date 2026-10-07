"""Mandatory locked check, byte-equal repeat and package-declared CLI smoke."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("component_contract", ROOT / "tools/component-contract.py")
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def build(descriptor, source):
    env = {key: value for key, value in os.environ.items()
           if key not in ("GH_TOKEN", "GITHUB_TOKEN", "CACHIX_AUTH_TOKEN", "SYMPHONY_RELEASE_SIGNING_KEY")}
    flags = ["--no-update-lock-file", "--no-write-lock-file", "--print-build-logs",
             "--cores", os.environ.get("NIX_BUILD_CORES", "1"), "--max-jobs", os.environ.get("NIX_MAX_JOBS", "1")]
    directory = "./" + descriptor["directory"]
    target = directory + "#" + descriptor["packageAttr"]
    def execute(args, capture=False):
        return subprocess.run(args, cwd=source, env=env, check=True,
                              stdout=subprocess.PIPE if capture else sys.stderr)
    execute(["nix", "flake", "check", directory, *flags])
    runtime = execute(["nix", "build", *flags, "--no-link", "--print-out-paths", target], True).stdout.decode().strip()
    contract.require(runtime and "\n" not in runtime, "Expected one runtime output")
    execute(["nix", "build", *flags, "--rebuild", "--keep-failed", "--no-link", target])
    smoke = descriptor["smoke"]
    contract.require(all(not (Path(runtime) / path).exists() for path in smoke["absentPaths"]),
                     "Volatile runtime metadata survived")
    result = execute([str(Path(runtime) / smoke["argv"][0]), *smoke["argv"][1:]], True)
    contract.require(smoke["contains"].encode() in result.stdout, "Runtime smoke failed")
    return runtime


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component")
    parser.add_argument("--source", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        print(build(contract.load(args.component), args.source))
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError):
        parser.exit(1, "Component build/repeat/smoke failed; candidate output withheld\n")

"""Select a trusted package source adapter; candidate execution stays keyless."""
import argparse
import importlib.util
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("component_release", ROOT / "tools/component-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def configure(component, root=ROOT):
    global source_adapter
    descriptor = release.configure(component, root)
    source_adapter = release.contract.adapter(descriptor, root)
    source_adapter.release = release
    release.require(source_adapter.RECIPE == descriptor["directory"] + "/default.nix",
                    "Source writer must target its declared package recipe")
    return source_adapter

WORKFLOW = f"{release.REPO}/.github/workflows/symphony-source.yml@refs/heads/main"


def candidate_run(args, source, check=True):
    env = {key: value for key, value in os.environ.items()
           if key not in (release.SIGNING_ENV, "GH_TOKEN", "GITHUB_TOKEN", "CACHIX_AUTH_TOKEN")}
    result = subprocess.run(args, cwd=source, capture_output=True, env=env)
    release.require(not check or result.returncode == 0, "Candidate command failed; output withheld")
    return result


def package_info(source):
    return json.loads(candidate_run(["nix", "eval", "--json", "--no-update-lock-file", "--no-write-lock-file",
        "--option", "allow-import-from-derivation", "false", "--apply",
        "p: { inherit (p) version drvPath; storePath = p.outPath; }",
        f"path:{source / release.DIRECTORY}#{release.DESCRIPTOR['packageAttr']}"], source).stdout)


def component_digest(source):
    return release.sha({path: hashlib.sha256((source / path).read_bytes()).hexdigest() for path in release.FILES})


def receipt():
    value = {key: os.environ.get(key, "") for key in
             ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")}
    release.require(value["GITHUB_RUN_ID"].isdigit() and value["GITHUB_RUN_ATTEMPT"].isdigit()
                    and release.SHA.fullmatch(value["GITHUB_WORKFLOW_SHA"])
                    and value["GITHUB_WORKFLOW_REF"] == WORKFLOW, "Source proof requires the exact main workflow")
    return {**value, "component": release.COMPONENT}


def plan(source, baseline, selected, mode):
    return {"schema": 1, "mode": mode, "baselineComponentSha256": baseline["componentSha256"],
            "componentSha256": component_digest(source), "pins": selected,
            "package": package_info(source), "proof": receipt()}


def prepare(source):
    release.require(not release.run(["git", "-C", str(source), "status", "--porcelain"]).stdout,
                    "Source candidate must start clean")
    baseline = release.source_identity(source)
    selected = source_adapter.select(source)
    release.shape(selected, ("mode", "pins"), "source adapter selection")
    release.require(selected["mode"] in ("CURRENT", "PREPARED"), "Unknown source selection")
    if selected["mode"] == "PREPARED":
        # Mandatory common gate: no adapter can opt out of repeat/smoke.
        runtime = candidate_run(["bash", str(ROOT / "scripts/build-component"), release.COMPONENT], source).stdout.decode().strip()
    prepared = plan(source, baseline, selected["pins"], selected["mode"])
    if selected["mode"] == "PREPARED":
        release.require(runtime == prepared["package"]["storePath"], "Prepared component output differs")
    changed = release.run(["git", "-C", str(source), "diff", "--name-only"]).stdout.decode().splitlines()
    release.require(changed == ([] if selected["mode"] == "CURRENT" else [source_adapter.RECIPE]),
                    "Candidate changed outside declared recipe pins")
    return prepared


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        adapter = configure(args.component)
        release.write_json(args.output.resolve(), prepare(args.source.resolve()))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "Component source preparation failed; raw provider payloads withheld\n")

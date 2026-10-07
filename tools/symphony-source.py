#!/usr/bin/env python3
"""Prepare only Symphony's four literal source pins, with a component repeat gate."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("symphony_release", ROOT / "tools/symphony-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
RECIPE = "pkgs/symphony-ts/default.nix"
UPSTREAM = "TarasKosh/symphony-ts"
FAKE_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
WORKFLOW = f"{release.REPO}/.github/workflows/symphony-source.yml@refs/heads/main"
PATTERNS = {
    "version": r'(?m)^  version = "([^"]+)";',
    "rev": r'(?m)^    rev = "([^"]+)";',
    "srcHash": r'(?s)\bsrc = fetchFromGitHub \{[^{}]*?\n    hash = "([^"]+)";',
    "pnpmHash": r'(?s)\bpnpmDeps = pnpm_10.fetchDeps \{[^{}]*?\n    hash = "([^"]+)";',
}


def pins(source):
    result = {}
    for key, pattern in PATTERNS.items():
        matches = list(re.finditer(pattern, source))
        release.require(len(matches) == 1, "Symphony recipe no longer has four known literal pins")
        result[key] = matches[0].group(1)
    release.require('owner = "TarasKosh";' in source and 'repo = "symphony-ts";' in source,
                    "Unexpected Symphony upstream owner")
    release.require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", result["version"])
                    and release.SHA.fullmatch(result["rev"]), "Invalid Symphony version/revision")
    for key in ("srcHash", "pnpmHash"):
        value = result[key]
        release.require(re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", value), "Invalid fixed-output pin")
        raw = base64.b64decode(value[7:], validate=True)
        release.require(len(raw) == 32 and base64.b64encode(raw).decode() == value[7:], "Noncanonical hash")
    return result


def apply_pins(source, selected):
    release.require(set(selected) == set(PATTERNS), "Only four Symphony pins may be written")
    pins(source)
    replacements = [(re.search(pattern, source).span(1), selected[key]) for key, pattern in PATTERNS.items()]
    for (start, end), value in sorted(replacements, reverse=True):
        source = source[:start] + value + source[end:]
    release.require(pins(source) == selected, "Invalid prepared pins")
    return source


def dependency_hash(result, derivation):
    diagnostic = (result.stderr + result.stdout).decode(errors="replace")
    pattern = (r"hash mismatch in fixed-output derivation '" + re.escape(derivation)
               + r"':\s+specified:\s+" + re.escape(FAKE_HASH) + r"\s+got:\s+(sha256-[A-Za-z0-9+/]{43}=)")
    matches = re.findall(pattern, diagnostic)
    release.require(result.returncode != 0 and diagnostic.count("hash mismatch in fixed-output derivation") == 1
                    and len(matches) == 1, "Unknown pnpm dependency failure; no automatic dependency repair")
    return matches[0]


def upstream(endpoint):
    return json.loads(release.run(["gh", "api", f"repos/{UPSTREAM}/{endpoint}"]).stdout)


def candidate_run(args, source, check=True):
    # Candidate evaluation, dependency fetch and CLI never inherit API credentials.
    env = {key: value for key, value in os.environ.items()
           if key not in (release.SIGNING_ENV, "GH_TOKEN", "GITHUB_TOKEN")}
    result = subprocess.run(args, cwd=source, capture_output=True, env=env)
    release.require(not check or result.returncode == 0, "Candidate command failed; provider output withheld")
    return result


def package_info(source):
    return json.loads(candidate_run(["nix", "eval", "--json", "--no-update-lock-file", "--no-write-lock-file",
        "--option", "allow-import-from-derivation", "false", "--apply",
        "p: { inherit (p) version drvPath; storePath = p.outPath; }",
        f"path:{source / release.DIRECTORY}#symphony-ts"], source).stdout)


def component_digest(source):
    import hashlib
    return release.sha({path: hashlib.sha256((source / path).read_bytes()).hexdigest() for path in release.FILES})


def receipt():
    value = {key: os.environ.get(key, "") for key in
             ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")}
    release.require(value["GITHUB_RUN_ID"].isdigit() and value["GITHUB_RUN_ATTEMPT"].isdigit()
                    and release.SHA.fullmatch(value["GITHUB_WORKFLOW_SHA"])
                    and value["GITHUB_WORKFLOW_REF"] == WORKFLOW, "Source proof requires the exact main workflow")
    return value


def plan(source, baseline, selected, mode):
    return {"schema": 1, "mode": mode, "baselineComponentSha256": baseline["componentSha256"],
            "componentSha256": component_digest(source), "pins": selected,
            "package": package_info(source), "proof": receipt()}


def prepare(source):
    release.require(not release.run(["git", "-C", str(source), "status", "--porcelain"]).stdout,
                    "Source candidate must start clean")
    baseline = release.source_identity(source)
    recipe = source / RECIPE
    original = recipe.read_text()
    current = pins(original)
    revision = upstream("commits/main")["sha"]
    release.require(isinstance(revision, str) and release.SHA.fullmatch(revision), "Invalid upstream main head")
    if revision == current["rev"]:
        return plan(source, baseline, current, "CURRENT")
    package = upstream(f"contents/package.json?ref={revision}")
    release.require(package.get("encoding") == "base64" and package.get("size", 0) <= 65536,
                    "Invalid upstream package metadata")
    version = json.loads(base64.b64decode(package["content"], validate=False))["version"]
    fetched = json.loads(candidate_run(["nix", "store", "prefetch-file", "--json", "--unpack",
        f"https://github.com/{UPSTREAM}/archive/{revision}.tar.gz"], source).stdout)
    selected = {"version": version, "rev": revision, "srcHash": fetched["hash"], "pnpmHash": FAKE_HASH}
    recipe.write_text(apply_pins(original, selected))
    target = f"path:{source / release.DIRECTORY}#symphony-ts.pnpmDeps"
    flags = ["--no-update-lock-file", "--no-write-lock-file"]
    derivation = candidate_run(["nix", "eval", "--raw", *flags, target + ".drvPath"], source).stdout.decode().strip()
    release.require(release.valid_path(derivation) and derivation.endswith(".drv"), "Invalid pnpm dependency derivation")
    result = candidate_run(["nix", "build", *flags, "--no-link", "--cores", "1", "--max-jobs", "1", target], source, check=False)
    selected["pnpmHash"] = dependency_hash(result, derivation)
    recipe.write_text(apply_pins(original, selected))
    # This is the mandatory pre-source-publication gate, not cache publication.
    # Run the trusted script in the candidate worktree; never an upstream script.
    result = candidate_run([str(ROOT / "scripts/build-symphony-component")], source)
    prepared = plan(source, baseline, selected, "PREPARED")
    release.require(result.stdout.decode().strip() == prepared["package"]["storePath"], "Prepared component output differs")
    release.require(release.run(["git", "-C", str(source), "diff", "--name-only"]).stdout.decode().splitlines() == [RECIPE],
                    "Candidate changed outside the four source pins")
    return prepared


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        release.write_json(args.output.resolve(), prepare(args.source.resolve()))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "Symphony source preparation failed; raw provider payloads withheld\n")

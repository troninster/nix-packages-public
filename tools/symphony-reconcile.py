#!/usr/bin/env python3
"""One Symphony source writer; durable READY dedup and bounded factory wake-up."""

import argparse
import base64
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("symphony_source", Path(__file__).with_name("symphony-source.py"))
source_adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_adapter)
release = source_adapter.release
RETRY_SECONDS = 6 * 3600


def has_ready(source, package):
    ref = release.api(f"git/ref/heads/{release.CATALOG}", missing=True)
    if ref is None:
        return False
    commit = release.api(f"git/commits/{ref['object']['sha']}")
    tree = release.api(f"git/trees/{commit['tree']['sha']}?recursive=1")
    release.require(not tree.get("truncated") and len(tree["tree"]) <= 8192, "Incomplete READY catalog")
    for entry in tree["tree"]:
        if entry["type"] != "blob" or not entry["path"].startswith(release.RECORDS + "/"):
            continue
        name = entry["path"][len(release.RECORDS) + 1:]
        if name == "latest.json":
            continue
        release.require(release.HEX.fullmatch(name.removesuffix(".json")) and name.endswith(".json"),
                        "Unknown READY catalog entry")
        blob = release.api(f"git/blobs/{entry['sha']}")
        release.require(blob.get("encoding") == "base64" and blob.get("size", 0) <= 1024**2, "Invalid READY blob")
        record = release.validate_record(json.loads(base64.b64decode(blob["content"].replace("\n", ""), validate=True)), "READY")
        release.require(name == record["releaseId"] + ".json", "READY filename identity differs")
        if record["source"]["componentSha256"] == source["componentSha256"] and record["package"] == package:
            return True
    return False


def wake_factory(revision, new_source=False):
    endpoint = "actions/workflows/symphony-release.yml/runs?branch=main&event=workflow_dispatch"

    def owned(run):
        release.require(run.get("repository", {}).get("full_name") == release.REPO
                        and run.get("head_repository", {}).get("full_name") == release.REPO
                        and run.get("head_branch") == "main" and run.get("event") == "workflow_dispatch",
                        "Foreign factory receipt")

    # Look up active runs independently of lifetime history: an old waiting run
    # must not disappear behind the first page of newer completed attempts.
    for status in ("queued", "in_progress", "requested", "waiting", "pending"):
        runs = release.api(f"{endpoint}&status={status}&per_page=100")
        release.require(type(runs.get("total_count")) is int and runs["total_count"] <= 100
                        and runs["total_count"] == len(runs.get("workflow_runs", [])), "Incomplete active factory run list")
        for run in runs["workflow_runs"]:
            owned(run)
            release.require(run.get("status") == status, "Unexpected active factory receipt")
            return "factory-pending"
    completed = release.api(f"{endpoint}&status=completed&per_page=1")
    release.require(type(completed.get("total_count")) is int and completed["total_count"] >= 0
                    and len(completed.get("workflow_runs", [])) == min(completed["total_count"], 1),
                    "Invalid newest completed factory receipt")
    latest = None
    for run in completed["workflow_runs"]:
        owned(run)
        release.require(run.get("status") == "completed", "Unexpected completed factory receipt")
        created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        release.require(created.tzinfo is not None, "Invalid factory receipt time")
        latest = created
    if not new_source and latest is not None and (datetime.now(timezone.utc) - latest).total_seconds() < RETRY_SECONDS:
        return "factory-retry-cooldown"
    release.approved_ancestor(revision)
    release.run(["gh", "api", f"repos/{release.REPO}/actions/workflows/symphony-release.yml/dispatches",
                 "--method", "POST", "--input", "-"],
                data=release.canonical({"ref": "main", "inputs": {"source_revision": revision}}))
    return "factory-dispatched"


def publish_source(candidate, prepared, fresh):
    release.shape(prepared, ("schema", "mode", "baselineComponentSha256", "componentSha256", "pins", "package", "proof"), "source preparation")
    release.require(prepared["schema"] == 1 and prepared["mode"] in ("CURRENT", "PREPARED")
                    and prepared["proof"] == source_adapter.receipt(), "Invalid source preparation proof")
    release.run(["git", "-C", str(candidate), "fetch", "origin", "main"])
    release.run(["git", "-C", str(candidate), "worktree", "add", "--detach", str(fresh), "origin/main"])
    baseline = release.source_identity(fresh)
    release.require(baseline["componentSha256"] == prepared["baselineComponentSha256"],
                    "Symphony changed on main; preserve prepared evidence and reconcile again")
    recipe = fresh / source_adapter.RECIPE
    recipe.write_text(source_adapter.apply_pins(recipe.read_text(), prepared["pins"]))
    release.require(source_adapter.component_digest(fresh) == prepared["componentSha256"],
                    "Fresh-main prepared component digest differs")
    if prepared["mode"] == "CURRENT":
        release.require(prepared["componentSha256"] == baseline["componentSha256"], "CURRENT changed source pins")
        return baseline["revision"], baseline
    release.run(["git", "-C", str(fresh), "diff", "--check"])
    release.require(release.run(["git", "-C", str(fresh), "diff", "--name-only"]).stdout.decode().splitlines()
                    == [source_adapter.RECIPE], "Source publication changed unrelated files")
    release.run(["git", "-C", str(fresh), "config", "user.name", "github-actions[bot]"])
    release.run(["git", "-C", str(fresh), "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    release.run(["git", "-C", str(fresh), "add", "--", source_adapter.RECIPE])
    release.run(["git", "-C", str(fresh), "commit", "-m", f"chore(symphony): pin {prepared['pins']['rev'][:12]}"])
    revision = release.run(["git", "-C", str(fresh), "rev-parse", "HEAD"]).stdout.decode().strip()
    # A late main advancement rejects this push; never force or roll back main.
    release.run(["git", "-C", str(fresh), "-c", "credential.helper=", "-c",
                 "credential.helper=!gh auth git-credential", "push", "origin", "HEAD:refs/heads/main"])
    return revision, None


def reconcile(candidate, prepared, fresh):
    revision, source = publish_source(candidate, prepared, fresh)
    if source is not None and has_ready(source, prepared["package"]):
        return "component-already-READY"
    return wake_factory(revision, new_source=prepared["mode"] == "PREPARED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("preparation", type=Path)
    parser.add_argument("fresh", type=Path)
    args = parser.parse_args()
    try:
        print(reconcile(args.candidate.resolve(), json.loads(args.preparation.read_bytes()), args.fresh.resolve()))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "Symphony reconciliation failed; raw provider payloads withheld\n")


if __name__ == "__main__":
    main()

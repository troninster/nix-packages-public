#!/usr/bin/env python3
"""Symphony-only signed runtime factory; READY requires a separate cold proof."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.parse import quote


REPO = "troninster/nix-packages-public"
COMPONENT = "symphony-ts"
PLATFORM = "x86_64-linux"
DIRECTORY = "pkgs/symphony-ts"
FILES = tuple(f"{DIRECTORY}/{name}" for name in ("default.nix", "flake.nix", "flake.lock"))
PUBLIC_KEY = "troninster-symphony-releases-1:NfUkV38l0uXARb9bm9ebjrmUmsiQcMjua9Qg+jxumQk="
SIGNING_ENV = "SYMPHONY_RELEASE_SIGNING_KEY"
PART_SIZE = 1536 * 1024**2
MAX_PARTS = 16
SHA = re.compile(r"[0-9a-f]{40}")
HEX = re.compile(r"[0-9a-f]{64}")
STORE = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+")
NAR = re.compile(r"nar/[0-9abcdfghijklmnpqrsvwxyz]{52}\.nar\.zst")
PART = re.compile(r"runtime\.tar\.part[0-9]{3}")
CATALOG = "component-releases"
RECORDS = f"releases/{COMPONENT}/{PLATFORM}"
WORKFLOW = f"{REPO}/.github/workflows/symphony-release.yml@refs/heads/main"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value))


def run(args, check=True, data=None):
    # The signing key is never inherited by a subprocess, including failures.
    env = {key: value for key, value in os.environ.items() if key != SIGNING_ENV}
    result = subprocess.run(args, input=data, capture_output=True, env=env)
    require(not check or result.returncode == 0,
            f"{args[0]} failed (exit {result.returncode}); provider/tool output withheld")
    return result


def shape(value, fields, context):
    require(isinstance(value, dict) and set(value) == set(fields), f"Invalid {context} fields")


def valid_path(path):
    return isinstance(path, str) and STORE.fullmatch(path) is not None


def identity(record):
    return {key: record[key] for key in ("component", "platform", "source", "package", "closure")}


def validate_record(record, stage):
    shape(record, ("schema", "status", "component", "platform", "source", "package", "closure",
                   "verification", "releaseId"), "record")
    require(type(record["schema"]) is int and record["schema"] == 1 and record["status"] == stage
            and record["component"] == COMPONENT and record["platform"] == PLATFORM, "Invalid record identity")
    source = record["source"]
    shape(source, ("repository", "revision", "directory", "componentSha256", "lockSha256", "upstreamRevision"), "source")
    require(source["repository"] == REPO and source["directory"] == DIRECTORY, "Invalid source owner")
    require(all(isinstance(source[k], str) and SHA.fullmatch(source[k]) for k in ("revision", "upstreamRevision"))
            and all(isinstance(source[k], str) and HEX.fullmatch(source[k]) for k in ("componentSha256", "lockSha256")),
            "Invalid source digests")
    package = record["package"]
    shape(package, ("version", "drvPath", "storePath"), "package")
    require(isinstance(package["version"], str) and 0 < len(package["version"]) <= 128
            and valid_path(package["drvPath"]) and package["drvPath"].endswith(".drv")
            and valid_path(package["storePath"])
            and Path(package["storePath"]).name[33:] == f"symphony-ts-{package['version']}",
            "Invalid Symphony package")
    closure = record["closure"]
    require(isinstance(closure, dict) and 0 < len(closure) <= 4096 and package["storePath"] in closure,
            "Invalid runtime closure")
    for path, entry in closure.items():
        require(valid_path(path) and not path.endswith((".drv", "-source")) and "-neurobooks-" not in path,
                "Non-runtime/private path in public closure")
        shape(entry, ("narHash", "narSize", "references"), "closure entry")
        value = entry["narHash"]
        require(isinstance(value, str) and value.startswith("sha256-"), "Invalid NAR hash")
        raw = base64.b64decode(value[7:], validate=True)
        require(len(raw) == 32 and base64.b64encode(raw).decode() == value[7:], "Noncanonical NAR hash")
        require(type(entry["narSize"]) is int and entry["narSize"] >= 0, "Invalid NAR size")
        refs = entry["references"]
        require(isinstance(refs, list) and all(valid_path(ref) and ref in closure for ref in refs)
                and refs == sorted(set(refs)), "Invalid runtime references")
    reached, pending = set(), [package["storePath"]]
    while pending:
        path = pending.pop()
        if path not in reached:
            reached.add(path)
            pending.extend(closure[path]["references"])
    require(reached == set(closure), "Unreachable exported runtime")
    shape(record["verification"], ("policy", "repeatBuild", "coldImport", "smoke"), "verification")
    require(record["verification"] == {"policy": "symphony-repeat-cold-v1", "repeatBuild": True,
                                       "coldImport": stage == "READY", "smoke": True}, "Incomplete verification")
    require(record["releaseId"] == sha(identity(record)), "Release identity mismatch")
    return record


def source_identity(source):
    revision = run(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.decode().strip()
    require(SHA.fullmatch(revision), "Source is not an exact Git revision")
    files = {path: (source / path).read_bytes() for path in FILES}
    for path, content in files.items():
        require(content == run(["git", "-C", str(source), "show", f"{revision}:{path}"]).stdout,
                "Source worktree differs from its exact revision")
    lock = json.loads(files[f"{DIRECTORY}/flake.lock"])
    require(set(lock["nodes"]) == {"root", "nixpkgs"}
            and lock["nodes"]["root"]["inputs"] == {"nixpkgs": "nixpkgs"}
            and "inputs" not in lock["nodes"]["nixpkgs"], "Component toolchain is not independently locked")
    upstream = re.findall(rb'\brev\s*=\s*"([0-9a-f]{40})"\s*;', files[f"{DIRECTORY}/default.nix"])
    require(len(upstream) == 1, "Expected one pinned upstream revision")
    return {"repository": REPO, "revision": revision, "directory": DIRECTORY,
            "componentSha256": sha({path: hashlib.sha256(body).hexdigest() for path, body in files.items()}),
            "lockSha256": hashlib.sha256(files[f"{DIRECTORY}/flake.lock"]).hexdigest(),
            "upstreamRevision": upstream[0].decode()}


def evaluated_package(source):
    return json.loads(run(["nix", "eval", "--json", "--no-update-lock-file", "--no-write-lock-file",
                           "--option", "allow-import-from-derivation", "false", "--apply",
                           "p: { inherit (p) version drvPath; storePath = p.outPath; }",
                           f"path:{source / DIRECTORY}#symphony-ts"]).stdout)


def closure_info(root, store=None):
    args = ["nix", "path-info", "--json", "--recursive"]
    if store:
        args += ["--store", store]
    result = json.loads(run([*args, root]).stdout)
    require(isinstance(result, dict), "Unsupported path-info graph")
    return {path: {"narHash": entry["narHash"], "narSize": entry["narSize"],
                   "references": sorted(entry["references"])} for path, entry in result.items()}


def no_build_options():
    return ["--option", "builders", "", "--max-jobs", "0", "--option", "substituters", ""]


def trust_options():
    return ["--option", "trusted-public-keys", PUBLIC_KEY, "--option", "require-sigs", "true"]


def proof(record, delivery, stage):
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    workflow_sha = os.environ.get("GITHUB_WORKFLOW_SHA", "")
    require(run_id.isdigit() and attempt.isdigit(), "Proof requires an exact workflow run/attempt")
    require(os.environ.get("GITHUB_WORKFLOW_REF") == WORKFLOW and SHA.fullmatch(workflow_sha),
            "Proof requires the reviewed main workflow identity")
    return {"schema": 1, "stage": stage, "runId": run_id, "runAttempt": attempt,
            "job": {"BUILT": "build", "SIGNED": "sign", "READY": "cold"}[stage],
            "workflowRef": WORKFLOW, "workflowRevision": workflow_sha,
            "releaseId": record["releaseId"], "recordSha256": sha(record), "deliverySha256": sha(delivery)}


def check_proof(record, delivery, receipt, stage):
    require(receipt == proof(record, delivery, stage), "Artifact does not match this run/attempt/stage")


def archive_cache(cache, directory):
    archive = directory / "runtime.tar"
    with tarfile.open(archive, "w") as bundle:
        for member in sorted(cache.rglob("*")):
            require(not member.is_symlink(), "Symlink in runtime filecache")
            if not member.is_file():
                continue
            entry = bundle.gettarinfo(str(member), member.relative_to(cache).as_posix())
            entry.uid = entry.gid = entry.mtime = 0
            entry.uname = entry.gname = ""
            with member.open("rb") as stream:
                bundle.addfile(entry, stream)
    archive_hash = digest(archive)
    parts = []
    if archive.stat().st_size <= PART_SIZE:
        part = directory / "runtime.tar.part000"
        archive.rename(part)
        parts.append({"name": part.name, "size": part.stat().st_size, "sha256": archive_hash})
    else:
        with archive.open("rb") as source:
            while source.tell() < archive.stat().st_size:
                require(len(parts) < MAX_PARTS, "Runtime exceeds Symphony release asset capacity")
                part = directory / f"runtime.tar.part{len(parts):03}"
                remaining = PART_SIZE
                with part.open("xb") as destination:
                    while remaining and (block := source.read(min(1024**2, remaining))):
                        destination.write(block)
                        remaining -= len(block)
                parts.append({"name": part.name, "size": part.stat().st_size, "sha256": digest(part)})
        archive.unlink()
    return archive_hash, parts


def export_bundle(record, directory, store=None):
    directory.mkdir(parents=True, exist_ok=False)
    root = record["package"]["storePath"]
    with tempfile.TemporaryDirectory(prefix="symphony-export-") as temporary:
        cache = Path(temporary) / "cache"
        args = ["nix", "copy", "--to", cache.as_uri() + "?compression=zstd", *no_build_options()]
        if store:
            args += ["--from", store]
        run([*args, root])
        archive_hash, parts = archive_cache(cache, directory)
    delivery = {"schema": 1, "component": COMPONENT, "platform": PLATFORM,
                "releaseId": record["releaseId"], "storePath": root,
                "archiveSha256": archive_hash, "parts": parts, "closure": record["closure"]}
    write_json(directory / "record.json", record)
    write_json(directory / "release.json", delivery)
    write_json(directory / "proof.json", proof(record, delivery, record["status"]))


def export_built(source, root, directory):
    package = evaluated_package(source)
    require(package["storePath"] == root, "Build result differs from evaluated component output")
    record = {"schema": 1, "status": "BUILT", "component": COMPONENT, "platform": PLATFORM,
              "source": source_identity(source), "package": package, "closure": closure_info(root),
              "verification": {"policy": "symphony-repeat-cold-v1", "repeatBuild": True,
                               "coldImport": False, "smoke": True}}
    record["releaseId"] = sha(identity(record))
    validate_record(record, "BUILT")
    export_bundle(record, directory)


def load_bundle(directory, stage):
    record = validate_record(json.loads((directory / "record.json").read_bytes()), stage)
    delivery = json.loads((directory / "release.json").read_bytes())
    shape(delivery, ("schema", "component", "platform", "releaseId", "storePath", "archiveSha256", "parts", "closure"), "delivery")
    require(delivery["schema"] == 1 and delivery["component"] == COMPONENT and delivery["platform"] == PLATFORM
            and delivery["releaseId"] == record["releaseId"] and delivery["storePath"] == record["package"]["storePath"]
            and delivery["closure"] == record["closure"] and isinstance(delivery["archiveSha256"], str)
            and HEX.fullmatch(delivery["archiveSha256"]), "Delivery identity mismatch")
    require(isinstance(delivery["parts"], list) and 0 < len(delivery["parts"]) <= MAX_PARTS, "Invalid parts")
    for index, part in enumerate(delivery["parts"]):
        shape(part, ("name", "size", "sha256"), "part")
        require(part["name"] == f"runtime.tar.part{index:03}" and type(part["size"]) is int
                and 0 < part["size"] <= PART_SIZE and isinstance(part["sha256"], str) and HEX.fullmatch(part["sha256"]),
                "Invalid runtime part")
        path = directory / part["name"]
        require(not path.is_symlink() and path.stat().st_size == part["size"] and digest(path) == part["sha256"],
                "Runtime part bytes differ")
    require({path.name for path in directory.iterdir()} == {"record.json", "release.json", "proof.json", *(p["name"] for p in delivery["parts"])},
            "Unexpected candidate artifact files")
    check_proof(record, delivery, json.loads((directory / "proof.json").read_bytes()), stage)
    return record, delivery


def unpack(directory, delivery, cache, signed):
    cache.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryFile() as archive:
        hasher = hashlib.sha256()
        for part in delivery["parts"]:
            with (directory / part["name"]).open("rb") as stream:
                for block in iter(lambda: stream.read(1024**2), b""):
                    hasher.update(block)
                    archive.write(block)
        require(hasher.hexdigest() == delivery["archiveSha256"], "Combined archive digest differs")
        archive.seek(0)
        expected = {Path(path).name[:32] + ".narinfo": path for path in delivery["closure"]}
        names, infos = set(), {}
        with tarfile.open(fileobj=archive, mode="r:") as bundle:
            for member in bundle:
                name = member.name
                require(member.isfile() and name not in names and
                        (name == "nix-cache-info" or name in expected or NAR.fullmatch(name)),
                        "Archive contains a non-runtime/duplicate/unsafe member")
                names.add(name)
                if name == "nix-cache-info" or name in expected:
                    require(member.size <= 65536, "Oversized cache metadata")
                target = cache / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open("xb") as destination:
                    for block in iter(lambda: source.read(1024**2), b""):
                        destination.write(block)
                if name in expected:
                    fields = {}
                    for line in target.read_text().splitlines():
                        key, separator, value = line.partition(": ")
                        require(separator, "Invalid narinfo field")
                        if key != "Sig":
                            require(key not in fields, "Duplicate narinfo field")
                            fields[key] = value
                        elif value.startswith(PUBLIC_KEY.split(":", 1)[0] + ":"):
                            fields["approvedSignature"] = True
                    infos[name] = fields
        nar_names = set()
        for name, path in expected.items():
            require(name in infos, "Missing runtime narinfo")
            fields, entry = infos[name], delivery["closure"][path]
            hash_value = fields.get("NarHash", "")
            sri = run(["nix", "hash", "convert", "--hash-algo", "sha256", "--to", "sri", hash_value]).stdout.decode().strip()
            require(fields.get("StorePath") == path and sri == entry["narHash"]
                    and fields.get("NarSize") == str(entry["narSize"])
                    and sorted("/nix/store/" + ref for ref in fields.get("References", "").split()) == entry["references"]
                    and fields.get("Compression") == "zstd" and NAR.fullmatch(fields.get("URL", ""))
                    and fields["URL"] in names and (not signed or fields.get("approvedSignature")),
                    "Narinfo differs from the runtime graph/signing authority")
            nar_names.add(fields["URL"])
        require(names == {"nix-cache-info", *expected, *nar_names}, "Unexpected or missing cache object")
        require("StoreDir: /nix/store" in (cache / "nix-cache-info").read_text().splitlines(), "Invalid cache store directory")
    return cache.as_uri()


def isolated_store(directory):
    require(not directory.exists(), "Isolated store must start absent")
    return "local?root=" + quote(str(directory), safe="/")


def import_runtime(directory, record, delivery, store, signed):
    with tempfile.TemporaryDirectory(prefix="symphony-import-") as temporary:
        cache = unpack(directory, delivery, Path(temporary) / "cache", signed)
        options = [*no_build_options(), *trust_options()] if signed else no_build_options()
        run(["nix", "store", "verify", "--store", cache, "--recursive",
             *( ["--sigs-needed", "1"] if signed else ["--no-trust"] ), *options, delivery["storePath"]])
        run(["nix", "copy", "--from", cache, "--to", store,
             *( [] if signed else ["--no-check-sigs"] ), *options, delivery["storePath"]])
    require(closure_info(delivery["storePath"], store) == record["closure"], "Imported runtime graph differs")
    run(["nix", "store", "verify", "--store", store, "--recursive",
         *( ["--sigs-needed", "1"] if signed else ["--no-trust"] ), *options, delivery["storePath"]])


def prepare_sign(directory, source, store_root):
    record, delivery = load_bundle(directory, "BUILT")
    require(source_identity(source) == record["source"] and evaluated_package(source) == record["package"],
            "Candidate differs from its reviewed exact source/package")
    store = isolated_store(store_root)
    import_runtime(directory, record, delivery, store, False)


def sign(directory, store_root):
    record, _ = load_bundle(directory, "BUILT")
    store = "local?root=" + quote(str(store_root), safe="/")
    require(closure_info(record["package"]["storePath"], store) == record["closure"], "Unsigned imported graph differs")
    key = os.environ.pop(SIGNING_ENV, "")
    require(key.startswith(PUBLIC_KEY.split(":", 1)[0] + ":"), "Approved signing key is unavailable")
    descriptor, filename = tempfile.mkstemp(prefix="symphony-signing-key-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(key)
        key = ""
        run(["nix", "store", "sign", "--store", store, "--key-file", filename,
             "--recursive", record["package"]["storePath"]])
    finally:
        Path(filename).unlink()


def export_signed(directory, store_root, output):
    record, _ = load_bundle(directory, "BUILT")
    store = "local?root=" + quote(str(store_root), safe="/")
    require(closure_info(record["package"]["storePath"], store) == record["closure"], "Signed graph differs")
    run(["nix", "store", "verify", "--store", store, "--recursive", "--sigs-needed", "1",
         *no_build_options(), *trust_options(), record["package"]["storePath"]])
    record["status"] = "SIGNED"
    export_bundle(record, output, store)


def cold(directory, store_root, output):
    record, delivery = load_bundle(directory, "SIGNED")
    store = isolated_store(store_root)
    import_runtime(directory, record, delivery, store, True)
    # Ubuntu's shell/unshare/mount live outside the store being hidden. No warm
    # /nix/store or network is available to the actual Node runtime smoke.
    smoke = run(["sudo", "/usr/bin/unshare", "--mount", "--net", "--propagation", "private",
                 "/bin/bash", "-eu", "-c",
                 'mount --bind "$1/nix/store" /nix/store; mount -o remount,bind,ro /nix/store; '
                 'export PATH=/usr/bin:/bin; "$2/bin/symphony" --help',
                 "symphony-cold", str(store_root), record["package"]["storePath"]])
    require(b"symphony" in smoke.stdout.lower(), "Cold CLI smoke failed")
    record["status"] = "READY"
    record["verification"]["coldImport"] = True
    validate_record(record, "READY")
    # Preserve the exact signed bytes that were cold-tested; never re-export
    # different transport after asserting the cold proof.
    output.mkdir(parents=True, exist_ok=False)
    for part in delivery["parts"]:
        with (directory / part["name"]).open("rb") as source, (output / part["name"]).open("xb") as destination:
            for block in iter(lambda: source.read(1024**2), b""):
                destination.write(block)
    write_json(output / "record.json", record)
    write_json(output / "release.json", delivery)
    write_json(output / "proof.json", proof(record, delivery, "READY"))


def api(endpoint, method="GET", payload=None, missing=False):
    args = ["gh", "api", f"repos/{REPO}/{endpoint}"]
    if method != "GET":
        args += ["--method", method]
    if payload is not None:
        args += ["--input", "-"]
    result = run(args, check=False, data=canonical(payload) if payload is not None else None)
    if missing and result.returncode and b"HTTP 404" in result.stderr:
        return None
    require(result.returncode == 0, "Owned GitHub publication/read failed; provider output withheld")
    return json.loads(result.stdout)


def approved_ancestor(revision):
    comparison = api(f"compare/{revision}...main")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("merge_base_commit", {}).get("sha") == revision,
            "Source revision is not an approved main ancestor")


def catalog_snapshot(record):
    ref = api(f"git/ref/heads/{CATALOG}", missing=True)
    if ref is None:
        return None, None, True, None
    revision = ref["object"]["sha"]
    require(SHA.fullmatch(revision), "Catalog ref is not immutable")
    commit = api(f"git/commits/{revision}")
    tree = api(f"git/trees/{commit['tree']['sha']}?recursive=1")
    require(not tree.get("truncated") and len(tree["tree"]) <= 8192, "Catalog tree is incomplete/oversized")
    prior, latest = {}, None
    for entry in tree["tree"]:
        path = entry["path"]
        if not path.startswith(RECORDS + "/") or entry["type"] != "blob":
            continue
        name = path[len(RECORDS) + 1:]
        require(name == "latest.json" or re.fullmatch(r"[0-9a-f]{64}\.json", name), "Unknown Symphony catalog entry")
        blob = api(f"git/blobs/{entry['sha']}")
        require(blob.get("encoding") == "base64" and blob.get("size", 0) <= 1024**2, "Invalid catalog blob")
        value = json.loads(base64.b64decode(blob["content"].replace("\n", ""), validate=True))
        if name == "latest.json":
            latest = value
        else:
            validate_record(value, "READY")
            require(name == value["releaseId"] + ".json", "Catalog record filename differs")
            for output, info in value["closure"].items():
                if output in record["closure"]:
                    require(info == record["closure"][output], "Conflicting bytes/references for an existing store path")
            prior[value["releaseId"]] = value
    if record["releaseId"] in prior:
        require(prior[record["releaseId"]] == record, "Immutable record already exists with different proof")
    promote = True
    if latest is not None:
        shape(latest, ("schema", "releaseId", "recordSha256"), "catalog latest")
        current = prior.get(latest["releaseId"])
        require(latest["schema"] == 1 and current is not None and sha(current) == latest["recordSha256"], "Catalog latest digest differs")
        comparison = api(f"compare/{current['source']['revision']}...{record['source']['revision']}")
        require(comparison.get("status") in ("ahead", "behind", "identical"), "Source history diverged")
        promote = comparison["status"] != "behind"
    return revision, commit["tree"]["sha"], promote, prior.get(record["releaseId"])


def release_assets(directory, record, delivery):
    tag = f"{COMPONENT}-{PLATFORM}-{record['releaseId']}"
    assets = [directory / "release.json", *(directory / part["name"] for part in delivery["parts"])]
    existing = api(f"releases/tags/{tag}", missing=True)
    if existing is None:
        run(["gh", "release", "create", tag, "--repo", REPO, "--target", record["source"]["revision"],
             "--draft", "--prerelease", "--latest=false", "--title", f"Signed Symphony {record['package']['version']}",
             "--notes", "Complete signed Symphony runtime; repeat build and isolated cold import verified.", *map(str, assets)])
        existing = api(f"releases/tags/{tag}")
    remote = {asset["name"]: asset for asset in existing["assets"]}
    require(set(remote) == {asset.name for asset in assets}, "Immutable release asset set differs")
    for asset in assets:
        entry = remote[asset.name]
        require(entry["state"] == "uploaded" and entry["size"] == asset.stat().st_size
                and entry.get("digest") == "sha256:" + digest(asset), "Immutable release asset digest differs")
    if existing["draft"]:
        api(f"releases/{existing['id']}", "PATCH", {"draft": False, "prerelease": True, "make_latest": "false"})
    return tag


def publish(directory):
    record, delivery = load_bundle(directory, "READY")
    approved_ancestor(record["source"]["revision"])
    revision, base_tree, promote, previous = catalog_snapshot(record)
    tag = release_assets(directory, record, delivery)
    if previous is not None and (not promote or previous["releaseId"] == record["releaseId"]):
        # Already catalogued immutable bytes; do not manufacture another attempt.
        return tag
    files = {f"{RECORDS}/{record['releaseId']}.json": record}
    if promote:
        files[f"{RECORDS}/latest.json"] = {"schema": 1, "releaseId": record["releaseId"], "recordSha256": sha(record)}
    entries = []
    for path, value in files.items():
        blob = api("git/blobs", "POST", {"content": base64.b64encode(canonical(value)).decode(), "encoding": "base64"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    payload = {"tree": entries}
    if base_tree:
        payload["base_tree"] = base_tree
    tree = api("git/trees", "POST", payload)
    commit = api("git/commits", "POST", {"message": f"release: Symphony {record['releaseId']}", "tree": tree["sha"],
                                        "parents": [revision] if revision else []})
    # Ref publication is a component-catalog CAS, not a foreign main-tip check.
    current = api(f"git/ref/heads/{CATALOG}", missing=True)
    require((current["object"]["sha"] if current else None) == revision, "Catalog advanced; preserve release and retry publication")
    if revision:
        api(f"git/refs/heads/{CATALOG}", "PATCH", {"sha": commit["sha"], "force": False})
    else:
        api("git/refs", "POST", {"ref": f"refs/heads/{CATALOG}", "sha": commit["sha"]})
    return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    built = commands.add_parser("export-built")
    built.add_argument("source", type=Path)
    built.add_argument("root")
    built.add_argument("output", type=Path)
    for name in ("prepare-sign", "sign", "export-signed", "cold"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "prepare-sign":
            command.add_argument("source", type=Path)
        command.add_argument("store_root", type=Path)
        if name in ("export-signed", "cold"):
            command.add_argument("output", type=Path)
    command = commands.add_parser("publish")
    command.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export-built":
            export_built(args.source.resolve(), args.root, args.output.resolve())
        elif args.command == "prepare-sign":
            prepare_sign(args.directory.resolve(), args.source.resolve(), args.store_root.resolve())
        elif args.command == "sign":
            sign(args.directory.resolve(), args.store_root.resolve())
        elif args.command == "export-signed":
            export_signed(args.directory.resolve(), args.store_root.resolve(), args.output.resolve())
        elif args.command == "cold":
            cold(args.directory.resolve(), args.store_root.resolve(), args.output.resolve())
        else:
            print(publish(args.directory.resolve()))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        parser.exit(1, "Symphony release failed; raw payloads and credentials withheld\n")


if __name__ == "__main__":
    main()

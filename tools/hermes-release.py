#!/usr/bin/env python3
"""Hermes-only signed runtime closure delivery. Never deletes cache objects."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = "troninster/nix-packages-public"
CACHES = ("https://cache.nixos.org", "https://troninster-nix-packages.cachix.org")
ROOT = re.compile(r"/nix/store/([0-9abcdfghijklmnpqrsvwxyz]{32})-hermes-agent-[0-9][A-Za-z0-9.+-]*")
PART_SIZE = 1536 * 1024**2


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    require(not check or result.returncode == 0,
            f"{args[0]} {args[1]} failed (exit {result.returncode}); raw output withheld")
    return result


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            result.update(block)
    return result.hexdigest()


def tag(path):
    match = ROOT.fullmatch(path)
    require(match is not None, "Only a Hermes package root is allowed")
    return "hermes-agent-x86_64-linux-" + match[1]


def core_derivation(data):
    # Nix 2.35 wraps the graph and uses store basenames; the host's older Nix
    # returns a flat full-path map. Both still expose the core build marker.
    graph = data.get("derivations", data)
    paths = [path for path, value in graph.items()
             if isinstance(value, dict) and value.get("env", {}).get("HERMES_NIX_BUILD") == "1"]
    require(len(paths) == 1, "Expected exactly one Hermes Python core")
    path = paths[0] if paths[0].startswith("/nix/store/") else "/nix/store/" + paths[0]
    require(path.endswith(".drv") and ROOT.fullmatch(path[:-4]), "Invalid Hermes core derivation")
    return path


def fields(payload):
    return dict(line.split(": ", 1) for line in payload.splitlines() if ": " in line)


def add_signatures(cache, path):
    narinfo = cache / (Path(path).name[:32] + ".narinfo")
    local = narinfo.read_text()
    if "\nSig: " in local:
        return
    expected = fields(local)
    for source in CACHES:
        request = Request(f"{source}/{narinfo.name}", headers={"User-Agent": "hermes-release/1"})
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read(1024**2 + 1)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise RuntimeError(f"Signature metadata unavailable (HTTP {exc.code})") from None
        require(len(data) <= 1024**2, "Signature metadata exceeds bounded read")
        remote = data.decode()
        actual = fields(remote)
        require(all(actual.get(key) == expected.get(key) for key in ("StorePath", "NarHash", "NarSize"))
                and set(actual.get("References", "").split()) == set(expected.get("References", "").split()),
                "Remote signature belongs to different runtime bytes/references")
        signatures = [line for line in remote.splitlines() if line.startswith("Sig: ")]
        if signatures:
            narinfo.write_text(local.rstrip() + "\n" + "\n".join(signatures) + "\n")
            return
    raise RuntimeError("Runtime output lacks a public signature; release refused")


def export(path, directory):
    tag(path)
    info = json.loads(run(["nix", "path-info", "--json", "--recursive", path]).stdout)
    require(path in info and all("-neurobooks-" not in p for p in info), "Invalid/public-private closure boundary")
    directory.mkdir(parents=True, exist_ok=False)
    cache = directory / "cache"
    # Explicit runtime root, never --all, derivations, source GC roots or host state.
    run(["nix", "copy", "--to", cache.as_uri() + "?compression=zstd", path])
    for output in sorted(info):
        add_signatures(cache, output)
    run(["nix", "store", "verify", "--store", cache.as_uri(), "--recursive", "--sigs-needed", "1",
         "--option", "substituters", "", path])
    archive = directory / "runtime.tar"
    with tarfile.open(archive, "w") as bundle:
        for member in sorted(cache.rglob("*")):
            if not member.is_file():
                continue
            entry = bundle.gettarinfo(str(member), member.relative_to(cache).as_posix())
            entry.uid = entry.gid = entry.mtime = 0
            entry.uname = entry.gname = ""
            with member.open("rb") as stream:
                bundle.addfile(entry, stream)
    parts = []
    with archive.open("rb") as source:
        index = 0
        while source.tell() < archive.stat().st_size:
            part = directory / f"runtime.tar.part{index:03}"
            remaining = PART_SIZE
            with part.open("xb") as destination:
                while remaining and (block := source.read(min(1024**2, remaining))):
                    destination.write(block)
                    remaining -= len(block)
            parts.append({"name": part.name, "size": part.stat().st_size, "sha256": digest(part)})
            index += 1
    manifest = {"schema": 1, "package": "hermes-agent", "platform": "x86_64-linux", "storePath": path,
                "producerBaseRevision": run(["git", "rev-parse", "HEAD"]).stdout.strip(),
                "lockSha256": digest(Path("flake.lock")), "archiveSha256": digest(archive), "parts": parts,
                "closure": {p: {"narHash": x["narHash"], "narSize": x["narSize"]} for p, x in info.items()}}
    (directory / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    # Only newly generated duplicates in this exact export directory. Evidence
    # and published parts remain; ready packages and Cachix are never deleted.
    archive.unlink()
    shutil.rmtree(cache)
    print(f"Prepared Hermes release: {len(info)} signed paths, {sum(p['size'] for p in parts)} archive bytes", flush=True)


def publish(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    path = manifest["storePath"]
    release_tag = tag(path)
    require(manifest.get("schema") == 1 and manifest.get("package") == "hermes-agent"
            and path in manifest.get("closure", {}), "Invalid Hermes manifest")
    assets = [directory / "manifest.json"]
    for part in manifest["parts"]:
        require(re.fullmatch(r"runtime\.tar\.part[0-9]{3}", part["name"]) is not None, "Invalid release asset")
        asset = directory / part["name"]
        require(asset.stat().st_size == part["size"] < 2 * 1024**3 and digest(asset) == part["sha256"],
                "Hermes release asset does not match verified export")
        assets.append(asset)
    probe = run(["gh", "api", f"repos/{REPO}/releases/tags/{release_tag}"], check=False)
    if probe.returncode == 0:
        existing = json.loads(probe.stdout)
        require(not existing["draft"], "Existing release is still a draft; preserve it for recovery")
        with urlopen(Request(f"https://github.com/{REPO}/releases/download/{release_tag}/manifest.json",
                             headers={"User-Agent": "hermes-release/1"}), timeout=30) as response:
            previous = json.load(response)
        require(previous["storePath"] == path and previous["closure"] == manifest["closure"],
                "Existing immutable Hermes release identity differs")
        uploaded = {a["name"]: a for a in existing["assets"]}
        require(set(uploaded) == {"manifest.json", *(p["name"] for p in previous["parts"])},
                "Existing Hermes release assets are incomplete")
        for part in previous["parts"]:
            asset = uploaded[part["name"]]
            require(asset["state"] == "uploaded" and asset["size"] == part["size"]
                    and asset.get("digest") == "sha256:" + part["sha256"],
                    "Existing Hermes release asset digest differs")
        print(f"Hermes release already published: {existing['html_url']}")
        return
    # Only a confirmed 404 permits creation; never turn API outages into a new release.
    require("404" in probe.stderr, "Cannot inspect existing Hermes release")
    run(["gh", "release", "create", release_tag, "--repo", REPO, "--target", manifest["producerBaseRevision"],
         "--draft", "--prerelease", "--latest=false", "--title", f"Signed Hermes runtime {Path(path).name}",
         "--notes", "Complete signed Hermes runtime closure. Nix signatures required; no cache eviction.",
         *map(str, assets)])
    releases = json.loads(run(["gh", "api", f"repos/{REPO}/releases?per_page=100"]).stdout)
    matches = [r for r in releases if r["tag_name"] == release_tag]
    require(len(matches) == 1 and matches[0]["draft"], "Could not resolve exact verified draft")
    release = matches[0]
    require({a["name"] for a in release["assets"]} == {a.name for a in assets}, "Draft assets are incomplete")
    for asset in release["assets"]:
        local = directory / asset["name"]
        require(asset["state"] == "uploaded" and asset["size"] == local.stat().st_size
                and asset.get("digest") == "sha256:" + digest(local), "GitHub upload digest mismatch")
    run(["gh", "api", "--method", "PATCH", f"repos/{REPO}/releases/{release['id']}",
         "-F", "draft=false", "-F", "prerelease=true", "-f", "make_latest=false"])
    print(f"Published https://github.com/{REPO}/releases/tag/{release_tag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("export")
    create.add_argument("path")
    create.add_argument("directory", type=Path)
    upload = sub.add_parser("publish")
    upload.add_argument("directory", type=Path)
    sub.add_parser("core-derivation")
    args = parser.parse_args()
    try:
        if args.command == "export":
            export(args.path, args.directory.resolve())
        elif args.command == "publish":
            publish(args.directory.resolve())
        else:
            print(core_derivation(json.load(sys.stdin)))
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Hermes release failed: {exc}\n")

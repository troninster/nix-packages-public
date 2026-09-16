#!/usr/bin/env python3
"""Repair the four Camofox workspace glob entries npm leaves stale."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys


EXPECTED_GLOB_OVERRIDES = {
    "@jest/reporters": "node_modules/@jest/reporters/node_modules/glob",
    "jest-config": "node_modules/jest-config/node_modules/glob",
    "jest-runtime": "node_modules/jest-runtime/node_modules/glob",
    "swagger-jsdoc": "node_modules/swagger-jsdoc/node_modules/glob",
}
TARGET_VERSION = "13.0.6"


def fail(message: str) -> None:
    raise ValueError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail(f"failed to read JSON {path}: {error}")
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def repair(lock_path: Path, package_json_path: Path) -> bool:
    package = load_json(package_json_path)
    lock = load_json(lock_path)
    if lock.get("lockfileVersion") != 3:
        fail(f"expected package-lock v3: {lock_path}")

    overrides = package.get("overrides")
    if overrides is None:
        # The currently pinned pre-1.14 source has no glob override and needs
        # no repair. Newer Camofox releases carry the targeted override map.
        return False
    if not isinstance(overrides, dict):
        fail("package.json overrides must be an object")

    present = set(EXPECTED_GLOB_OVERRIDES) & set(overrides)
    if not present:
        return False
    if present != set(EXPECTED_GLOB_OVERRIDES):
        fail("package.json has an incomplete Camofox glob override set")
    for parent in EXPECTED_GLOB_OVERRIDES:
        parent_override = overrides[parent]
        if not isinstance(parent_override, dict) or parent_override.get("glob") != TARGET_VERSION:
            fail(f"package.json override for {parent}/glob is not {TARGET_VERSION}")

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        fail("package-lock v3 packages must be an object")
    top_level = packages.get("node_modules/glob")
    if not isinstance(top_level, dict) or top_level.get("version") != TARGET_VERSION:
        fail(f"package-lock top-level glob must be {TARGET_VERSION}")

    nested_glob_paths = {
        path
        for path in packages
        if path.startswith("node_modules/") and path.endswith("/node_modules/glob")
    }
    expected_paths = set(EXPECTED_GLOB_OVERRIDES.values())
    if nested_glob_paths != expected_paths:
        unexpected = sorted(nested_glob_paths - expected_paths)
        missing = sorted(expected_paths - nested_glob_paths)
        fail(f"unexpected Camofox glob paths; missing={missing}, unexpected={unexpected}")

    changed = False
    for path in sorted(expected_paths):
        nested = packages.get(path)
        if not isinstance(nested, dict):
            fail(f"Camofox glob entry is not an object: {path}")
        repaired = deepcopy(top_level)
        for field in ("dev", "optional", "devOptional"):
            if field in nested:
                repaired[field] = nested[field]
        if nested != repaired:
            packages[path] = repaired
            changed = True

    if changed:
        lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} PACKAGE-LOCK PACKAGE-JSON", file=sys.stderr)
        return 2
    try:
        changed = repair(Path(argv[1]), Path(argv[2]))
    except ValueError as error:
        print(f"camofox package-lock repair failed: {error}", file=sys.stderr)
        return 1
    print("repaired Camofox glob entries" if changed else "Camofox glob entries already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

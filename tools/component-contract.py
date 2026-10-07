"""Trusted public component enrollment; descriptors never enter release identity."""

import argparse
import base64
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def relative(value):
    return isinstance(value, str) and value and not value.startswith("/") and all(
        part not in ("", ".", "..") for part in value.split("/"))


def validate(value):
    require(isinstance(value, dict) and set(value) == {
        "schema", "component", "repository", "directory", "platform", "packageAttr", "packageName",
        "verificationPolicy", "sourceFiles", "sourceAdapter", "smoke"}, "Invalid component descriptor fields")
    require(type(value["schema"]) is int and value["schema"] == 1, "Invalid descriptor schema")
    for key in ("component", "packageName", "verificationPolicy"):
        require(isinstance(value[key], str) and NAME.fullmatch(value[key]), "Invalid component admission")
    require(isinstance(value["platform"], str) and re.fullmatch(r"[a-z0-9_]+-[a-z0-9]+", value["platform"]),
            "Invalid component platform")
    require(value["repository"] == "troninster/nix-packages-public"
            and value["directory"] == "pkgs/" + value["component"]
            and re.fullmatch(r"[A-Za-z0-9_-]+", value["packageAttr"]), "Invalid component owner/output")
    files = value["sourceFiles"]
    require(isinstance(files, list) and len(files) == len(set(files)) and all(relative(path) for path in files)
            and all(value["directory"] + "/" + name in files for name in ("default.nix", "flake.nix", "flake.lock")),
            "Component source graph is incomplete")
    require(relative(value["sourceAdapter"]) and value["sourceAdapter"].startswith(value["directory"] + "/"),
            "Source adapter is not package-owned")
    smoke = value["smoke"]
    require(isinstance(smoke, dict) and set(smoke) == {"argv", "contains", "absentPaths"}
            and isinstance(smoke["argv"], list) and smoke["argv"] and relative(smoke["argv"][0])
            and all(isinstance(arg, str) for arg in smoke["argv"])
            and isinstance(smoke["contains"], str) and smoke["contains"]
            and isinstance(smoke["absentPaths"], list) and all(relative(path) for path in smoke["absentPaths"]),
            "Invalid mandatory smoke data")
    return value


def load(component, root=ROOT):
    require(isinstance(component, str) and NAME.fullmatch(component), "Invalid selected component")
    value = validate(json.loads((root / "components" / (component + ".json")).read_bytes()))
    require(value["component"] == component, "Descriptor filename identity differs")
    return value


def enrolled(root=ROOT):
    return [load(path.stem, root)["component"] for path in sorted((root / "components").glob("*.json"))]


def adapter(descriptor, root=ROOT):
    # Only the frozen trusted checkout supplies executable adapters, never candidate source.
    spec = importlib.util.spec_from_file_location("component_source_adapter", root / descriptor["sourceAdapter"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locked_graph(lock):
    require(isinstance(lock, dict) and lock.get("version") == 7 and isinstance(lock.get("nodes"), dict)
            and isinstance(lock.get("root"), str) and lock["root"] in lock["nodes"], "Invalid owned lock graph")
    nodes, root = lock["nodes"], lock["root"]

    def resolve(edge, resolving=()):
        if isinstance(edge, str):
            require(edge in nodes, "Missing locked node")
            return edge
        require(isinstance(edge, list) and edge and all(isinstance(item, str) for item in edge), "Invalid follows edge")
        key = tuple(edge)
        require(key not in resolving, "Cyclic follows edge")
        current = root
        for name in edge:
            inputs = nodes[current].get("inputs", {})
            require(name in inputs, "Missing follows input")
            current = resolve(inputs[name], (*resolving, key))
        return current

    seen, active = set(), set()

    def visit(name):
        require(name not in active, "Cyclic locked graph")
        if name in seen:
            return
        node = nodes[name]
        require(isinstance(node, dict) and isinstance(node.get("inputs", {}), dict), "Invalid lock node")
        if name != root:
            locked = node.get("locked")
            require(isinstance(locked, dict) and isinstance(locked.get("narHash"), str)
                    and re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", locked["narHash"])
                    and isinstance(locked.get("type"), str) and locked["type"], "Unlocked input")
            raw = base64.b64decode(locked["narHash"][7:], validate=True)
            require(len(raw) == 32 and base64.b64encode(raw).decode() == locked["narHash"][7:],
                    "Noncanonical locked input hash")
        active.add(name)
        for edge in node.get("inputs", {}).values():
            visit(resolve(edge))
        active.remove(name)
        seen.add(name)

    visit(root)
    require(seen == set(nodes), "Unreachable lock nodes")
    return lock


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "has"))
    parser.add_argument("component", nargs="?")
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps(enrolled(), separators=(",", ":")))
    else:
        raise SystemExit(0 if args.component in enrolled() else 1)

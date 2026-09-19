{ system, pinFile ? ./toolchain.json }:
let
  pin = builtins.fromJSON (builtins.readFile pinFile);
  goPkgs = import (builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/${pin.nixpkgs_rev}.tar.gz";
    sha256 = pin.nixpkgs_hash;
  }) { inherit system; };
  go = goPkgs.${pin.go_attr};
in
assert builtins.match "[0-9a-f]{40}" pin.nixpkgs_rev != null;
assert builtins.match "[0-9]+\\.[0-9]+\\.[0-9]+" pin.go_version != null;
assert go.version == pin.go_version;
goPkgs.buildGoModule.override { inherit go; }

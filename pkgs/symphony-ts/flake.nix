{
  description = "Independent Symphony TS component";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      symphony = pkgs.callPackage ./default.nix { };
    in
    {
      packages.${system} = {
        symphony-ts = symphony;
        default = symphony;
      };
      checks.${system}.symphony-ts = symphony;
    };
}

# Binary Cache

This repository can be used in two modes.

## Source Package Repository

Any Nix flake can consume this repository directly:

```nix
inputs.taras-packages.url = "github:troninster/nix-packages-public";
```

Without a binary cache, each consuming machine evaluates the flake and builds
missing packages locally.

## Binary Package Repository

To make GitHub-built binaries reusable, publish build outputs to a Nix binary
cache. Use one of these:

- Cachix - simplest hosted option.
- Attic - good self-hosted option.

For Cachix, configure repository secrets/variables:

- repository variable `CACHIX_CACHE_NAME`
- repository secret `CACHIX_AUTH_TOKEN`

Both `CI` and `Update Upstreams` run `scripts/build-package`, which explicitly
pushes the built package result link with `cachix push`. GitHub package builds
set `REQUIRE_CACHIX_PUSH=1`, so missing Cachix configuration or a failed push is
a workflow failure instead of a silent local-build trap.

Then add the resulting substituter and trusted public key to consuming NixOS
machines:

```nix
nix.settings.substituters = [
  "https://cache.nixos.org/"
  "https://<cache-name>.cachix.org"
];

nix.settings.trusted-public-keys = [
  "<cache-name>.cachix.org-1:<public-key>"
];
```

The public key is shown by the cache service after cache creation.

This flake also declares the same Cachix substituter in `nixConfig` so ad hoc
flake consumers can opt into it when Nix prompts for extra flake configuration.

## Require a Cached Package Locally

For heavy packages such as Codex, do not run a normal local build to check
availability. Use:

```sh
./scripts/require-cached-package codex
```

The script runs `nix build --no-link --max-jobs 0`, with this repository's
Cachix substituter and public key. If the package is not available in the cache,
the command fails instead of compiling it locally.

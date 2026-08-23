# nix-packages-public

Standalone Nix flake for packages that are useful outside the machine-specific
`dotfiles` repository.

The repository exports packages directly and as an overlay, so it can be used by
NixOS, Home Manager, or ad hoc `nix build` commands.

## Packages

- `archon` - Archon workflow engine CLI from the upstream GitHub release.
- `camofox-browser` - Camofox-backed browser REST API server for AI agents.
- `camoufox-agent` - CLI runner for the local FedotFox/Camoufox browser backend.
- `codex` - Codex CLI from the upstream OpenAI Codex release flake.
- `devspace` - MCP server for approved local coding workspaces.
- `freellmapi` - OpenAI-compatible proxy for free-tier LLM providers.
- `github-cli` - GitHub command line tool.
- `hermes-agent` - Hermes Agent from the upstream Hermes flake.
- `notion-cli` - Full-featured command line tool for Notion.
- `omp` - Oh My Pi coding-agent CLI from the official Linux release asset.
- `render-cli` - Render command line tool.
- `supabase-cli` - Supabase command line tool.
- `symphony-ts` - Autonomous implementation runs driven by a tracker.
- `vexora` - VEXORA system CLI skeleton for global installation wiring.

## Build

```sh
./scripts/build-all
```

Local builds are for small packages and emergency maintenance only. The
upstream Codex release build is memory-heavy and should be built by GitHub
Actions, then reused from Cachix on local machines.
The Codex package intentionally builds only the `codex` CLI and its required
`codex-code-mode-host` runtime sidecar instead of every binary in the upstream
Rust workspace. The sandbox-enabled sidecar requires the matching official
Codex rusty_v8 archive and source-binding pair for the Cargo.lock V8 version.

Before a local machine consumes Codex, require the cached output:

```sh
./scripts/require-cached-package codex
```

That command uses `--max-jobs 0`, so a cache miss fails instead of compiling
Codex locally. `./scripts/build-all` still evaluates flake checks first and then
builds each package separately; use it only when a local build is intentional.

## Consume From Another Flake

Add the input:

```nix
inputs.taras-packages.url = "github:troninster/nix-packages-public";
```

Use packages directly:

```nix
home.packages = [
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.archon
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.camofox-browser
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.camoufox-agent
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.codex
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.devspace
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.freellmapi
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.github-cli
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.hermes-agent
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.notion-cli
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.omp
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.render-cli
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.supabase-cli
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.symphony-ts
  inputs.taras-packages.packages.${pkgs.stdenv.hostPlatform.system}.vexora
];
```

Or add the overlay:

```nix
nixpkgs.overlays = [
  inputs.taras-packages.overlays.default
];
```

Then refer to:

```nix
pkgs.archon
pkgs.camofox-browser
pkgs.camoufox-agent
pkgs.codex
pkgs.devspace
pkgs.freellmapi
pkgs.github-cli
pkgs.hermes-agent
pkgs.notion-cli
pkgs.omp
pkgs.render-cli
pkgs.supabase-cli
pkgs.symphony-ts
pkgs.vexora
```

## Binary Cache

GitHub Actions detects the packages affected by each pull request, evaluates
the flake when Nix-related files changed, and builds only the selected package
jobs. Manual `workflow_dispatch` runs can build a targeted package, for example
`codex`.
The hosted GitHub runner only has 8 GB RAM, so the build jobs add
swap, mount expanded build space at `/nix`, and print periodic memory/disk
telemetry. When `CACHIX_CACHE_NAME` and `CACHIX_AUTH_TOKEN` are configured,
successful package jobs also push their result links to Cachix. CI package
builds require that push to succeed, so a green package build means the result
was published for local substitution.

For reusable binaries, configure Cachix or Attic and add the substituter to the
consuming NixOS machines. See `docs/binary-cache.md`.

## Upstream Updates

The `Update Upstreams` workflow runs every six hours and can also be started manually. It
checks the latest Archon release asset, the latest Codex `rust-v*` release tag,
the Hermes `main` input, the current `jo-inc/camofox-browser` default-branch
revision, and the latest `daijro/camoufox` Linux engine release asset, then
builds only the packages whose upstream pins changed. Camofox local runtime
patches are applied during the package build, so upstream drift fails in the
targeted build instead of silently dropping patches. The workflow commit uses
`[skip ci]`, so the regular CI workflow is not repeated after a successful
targeted update build.
Oh My Pi is intentionally updated through a reviewed fixed-hash package change,
not by this scheduled workflow.

## CI Package Selection

Local packages are discovered from `pkgs/*/default.nix`. Adding a new package
under `pkgs/<name>/default.nix` makes it available as `.#<name>` without editing
the shared package list in `flake.nix`. CI maps changes under `pkgs/<name>/` to
that package only, so adding `pkgs/example/default.nix` builds `example` instead
of rebuilding every package.

Manual CI runs accept an optional `packages` input with comma- or
space-separated package names. Set `all=true` only when a full package rebuild is
intended.

## Repository Boundaries

This repository owns reusable package definitions and small local tools needed
by those packages. Machine configuration stays in `dotfiles`; agent runtime
state stays outside this repository.

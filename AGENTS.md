# Agent Instructions

This repository is a standalone Nix package collection.

## Scope

- Package derivations live under `pkgs/`.
- Local package sources that are maintained here live under `tools/`.
- The flake exports `packages`, `checks`, and `overlays.default`.
- Do not put host-specific NixOS configuration, secrets, auth files, caches, or
  runtime state in this repository.

## Workflow

- Before edits, run `git status --short --branch`.
- Keep packages reproducible: pin upstream sources with hashes.
- Prefer fixed-output fetchers such as `fetchFromGitHub` or `fetchurl`.
- Run `nix flake check --print-build-logs` before publishing package changes.
- If a package is large, at least run `nix build .#<package> --no-link` for the
  package touched by the change.
- Run `gitleaks protect --staged --no-banner --redact` before committing.

## Current Packages

- `archon`
- `camofox-browser`
- `camoufox-agent`
- `codex`
- `devspace`
- `freellmapi`
- `github-cli`
- `hermes-agent`
- `notion-cli`
- `omp`
- `render-cli`
- `supabase-cli`
- `symphony-ts`
- `vexora`

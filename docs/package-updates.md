# Package Updates

## Automated Upstream Inputs

`.github/workflows/update-upstreams.yml` checks upstream package pins once per
six hours.
It updates:

- `archon` by discovering the latest upstream release and updating the Linux
  x86_64 release asset hash.
- `codex` by discovering the latest stable upstream `rust-v*` tag and updating
  `flake.nix` plus `flake.lock`.
- `hermes-agent` by updating the locked revision of the upstream `main` input
  as a compatibility signal for the declarative Hermes patch and package build.
- `camofox-browser` by updating the browser source pin and generated npm
  dependency hash.
- `camoufox engine` by updating the Linux x86_64 release asset pin.
- `symphony-ts` by following the owner fork's `main` branch and updating its
  source and pnpm dependency hashes when needed.

Only packages whose upstream pin changed are built. Codex is prepared and
published in its own lane first. If Codex preparation or its build fails, the
remaining lane still checks and builds independent upstreams such as Hermes; if
Codex preparation succeeds, the remaining lane waits for its fail-closed
publication before taking a fresh `main` snapshot. Each lane commits its pin
update only after every changed package in that lane passes. The commit message
includes `[skip ci]` so the regular CI workflow does not rebuild packages after
the targeted update.

Codex updates can still require manual maintenance when upstream Rust
dependency hashes or the prebuilt `rusty_v8` archive version changes. The
`codexCargoOutputHashes` keys in `flake.nix` must match the git-sourced package
names and versions in the selected Codex `codex-rs/Cargo.lock` exactly:
upstream additions need a hash, while upstream removals require deleting the
now-unused hash before `importCargoLock` can evaluate.

The Hermes `main` lock in this repository proves that the local patch still
applies and the package still builds; it is not a host promotion channel. A
downstream configuration can override the package input with a release-tagged
Hermes input through `follows`, and remains on that selected Hermes pin and its
own locked `nix-packages` revision until both are promoted and activated there.

Oh My Pi is packaged from its official x86_64 Linux release asset with a fixed
hash and exported as `omp`. It is updated manually by changing the
version and source hash under `pkgs/omp`, building `.#omp`, and reviewing the
resulting pull request; it is not part of the scheduled upstream workflow.

DevSpace is packaged from a pinned upstream source revision with fixed source
and npm dependency hashes. Update `pkgs/devspace` and review a successful
`.#devspace` build when promoting an upstream release.

## Pull Request CI Targeting

`.github/workflows/ci.yml` uses `scripts/detect-ci-packages` to compare the
base and head commits before creating the package build matrix.

- `pkgs/camofox-browser/**` builds `camofox-browser`.
- `pkgs/camoufox-agent/**` and `tools/camoufox-agent/**` build
  `camoufox-agent`.
- `pkgs/archon/**` builds `archon`.
- `flake.lock` changes build only the root input closure that changed:
  `codex`, `hermes-agent`, or all packages when the repository `nixpkgs` input
  changed.
- `flake.nix` changes build all packages because it can alter package wiring,
  overlays, or shared build arguments.
- CI workflow and common build-script changes build all packages because they
  can alter the build path for every package.
- Upstream-update workflow and script changes build the packages managed by that
  workflow.
- Documentation-only changes skip Nix evaluation and package builds, while the
  stable `CI result` job still reports success.

Manual CI runs still build every package.

## Manual Fixed-Output Updates

For fixed-output packages:

1. Update the package version and upstream revision or URL in `pkgs/<name>/`.
2. Temporarily set the changed source hash to `lib.fakeHash` or
   `lib.fakeSha256`.
3. Run:

   ```sh
   nix build .#<package> --print-build-logs
   ```

4. Copy the hash reported by Nix back into the package.
5. Run:

   ```sh
   nix flake check --print-build-logs
   gitleaks protect --staged --no-banner --redact
   ```

6. Commit and push.

For packages with generated dependency hashes, such as `buildNpmPackage`, update
both the source hash and the dependency hash reported by Nix.

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
- `devspace` and `freellmapi` by following their latest stable release tags
  and refreshing their source/npm dependency hashes.
- `github-cli` and `notion-cli` by following their latest stable release tags
  and refreshing their source/vendor hashes.
- `omp` by following the latest `omp-linux-x64` release asset.
- `supabase-cli` by following the latest stable release and refreshing the
  nested `apps/cli-go` source/vendor hashes.

Only packages whose upstream pin changed are built. Codex is prepared and
published in its own lane first. If Codex preparation or its build fails, the
remaining lane still checks and builds independent upstreams such as Hermes; if
Codex preparation succeeds, the remaining lane waits for its fail-closed
publication before taking a fresh `main` snapshot. Each lane commits its pin
update only after every changed package in that lane passes. The commit message
includes `[skip ci]` so the regular CI workflow does not rebuild packages after
the targeted update.

GitHub CLI release discovery, dependency-hash generation, build and publication
run in a separate lane after the remaining publisher. `--without-codex` is the
scheduled remaining lane and excludes GitHub CLI and Camofox discovery;
`--github-cli-only` updates `pkgs/github-cli/default.nix` and its data-only
`toolchain.json` pin. A failed GitHub
CLI candidate is rolled back and its job stays red, but earlier verified
Codex/remaining updates are already published. No failed candidate is accepted
and the publisher cannot change `flake.lock` or another package. The manual
no-argument updater still checks all packages and fails on any block failure.

Camofox source/engine discovery, candidate contracts, build and publication run
in their own final lane (`--camofox-only`). A browser compatibility failure
cannot prevent earlier verified packages from publishing. The narrow artifact
allows only `pkgs/camofox-browser/default.nix`; it cannot carry other package
changes. Historical compatibility fixtures remain fixed, while live pin tests
validate reproducibility rather than requiring an obsolete release number.

GitHub CLI owns an independently pinned Nixpkgs toolchain snapshot. Each poll
checks both the release and `nixos-unstable`, selects the newest stable Go in
that snapshot satisfying the candidate's `go` and `toolchain` requirements,
and pins the snapshot commit/hash plus the selected Go attribute/version.
Go and its transitive build dependencies can update even without a new `gh`
release; the updater never downgrades the compiler or selects a prerelease.
An unchanged compatible snapshot is a no-op. The package and overlay use the
same pinned builder; no mutable toolchain download occurs during compilation.

The source and vendor hashes are regenerated after a release or toolchain
change. Library versions remain those in upstream `go.mod`/`go.sum`, not
arbitrary `go get -u` upgrades. Both candidate files are restored on discovery
failure, and full build, CLI install check and cache publication still gate
the writer. If Nixpkgs has no compatible stable Go yet, that lane fails safely
and retries on a later poll; unsupported source/build-layout changes are not
automatically rewritten.

Supabase also owns an independent toolchain pin and uses the same Go discovery
and builder code as GitHub CLI, with `apps/cli-go/go.mod` as its requirement.
Its initial pin preserves the former Hermes-derived Nixpkgs/Go 1.26.5 snapshot;
later polls refresh source/vendor hashes after release or toolchain changes.
Hermes updates no longer rebuild Supabase. Changes to the shared Go helper or
builder still select both CLI consumers; each data-only pin selects its owner.

Codex updates can still require manual maintenance when upstream Rust
dependency hashes or the prebuilt `rusty_v8` archive version changes. The
`codexCargoOutputHashes` keys in `flake.nix` must match the git-sourced package
names and versions in the selected Codex `codex-rs/Cargo.lock` exactly:
upstream additions need a hash, while upstream removals require deleting the
now-unused hash before `importCargoLock` can evaluate.

The Codex build patches vendored `i18n-embed-fl` 0.9.4 to sort named arguments
before emitting code. Its former HashMap traversal changed the `age` error
formatter between identical builds, producing different NAR hashes for one
store path. The patch applies with zero fuzz; review it when that dependency
changes. Runtime hash randomization and artifact integrity checks stay intact.
For an explicit remote reproducibility check, dispatch `ci.yml` with
`packages=codex`, `all=false`, and `check_reproducibility=true`. The additional
`nix build --rebuild` must match the first output byte-for-byte; it is not run
by ordinary PR or upstream-update jobs.

The Hermes `main` lock in this repository proves that the local patch still
applies and the package still builds; it is not a host promotion channel. A
downstream configuration can override the package input with a release-tagged
Hermes input through `follows`, and remains on that selected Hermes pin and its
own locked `nix-packages` revision until both are promoted and activated there.

Oh My Pi is packaged from its official x86_64 Linux release asset with a fixed
hash and exported as `omp`. The scheduled workflow updates only the
`omp-linux-x64` asset and still requires the package build before publication.

DevSpace is packaged from a pinned upstream source revision with fixed source
and npm dependency hashes. Its updater refuses a release whose package-lock
shape no longer matches the integrity repair in `pkgs/devspace/default.nix`;
that is an intentional manual-maintenance stop rather than a speculative
lockfile rewrite.

FreeLLMAPI intentionally builds and installs only its server and client
workspaces. A new upstream CLI workspace must not silently expand the package
surface.

Supabase CLI is built from the upstream `apps/cli-go` module. A release that
moves that module or changes its version ldflags must stop the updater for
manual review.

Render CLI remains manually updated and is deliberately outside the scheduled
upstream workflow. Local `vexora` and `camoufox-agent` sources have no external
release lane and are also excluded.

## Pull Request CI Targeting

Normal heavy package builds and Cachix publication are owned by GitHub Actions.
The weak local host runs lightweight tests and parsing checks and consumes the
published outputs; a local heavy build is only a bounded incident-diagnostic
exception, not the standard release gate.

`.github/workflows/ci.yml` uses `scripts/detect-ci-packages` to compare the
base and head commits before creating the package build matrix.

- `pkgs/camofox-browser/**` builds `camofox-browser`.
- `pkgs/camoufox-agent/**` and `tools/camoufox-agent/**` build
  `camoufox-agent`.
- `pkgs/archon/**` builds `archon`.
- The registration-lifecycle helper and its focused tests build `hermes-agent`.
- `flake.lock` changes build only the root input closure that changed:
  `codex`, `hermes-agent`, or all packages when the repository `nixpkgs` input
  changed. A root `rust-overlay` change selects Codex, including its `follows`
  dependency edge.
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

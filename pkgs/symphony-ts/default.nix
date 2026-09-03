{
  lib,
  stdenv,
  fetchFromGitHub,
  makeWrapper,
  nodejs_22,
  pnpm_10,
  ...
}:

# Forked upstream, two links down: OasAIStudio/symphony-ts is the Apache-2.0
# root, dormant since 2026-03; Chetvertkov/symphony-ts is the live upstream,
# 21 commits ahead of it and where the owner's contributions land; this pins the
# owner's fork of that. Packaged from
# source. There is no published npm distribution and no release tag, so the pin
# is a commit on the fork's main branch.
#
# Axis A `forked`, Axis B `occasionally-patched` — a component, not a project.
# See vexora/docs/adr/2026-07-22-component-project-two-axis-model.md.
stdenv.mkDerivation (finalAttrs: {
  pname = "symphony-ts";
  version = "0.1.8";

  src = fetchFromGitHub {
    owner = "TarasKosh";
    repo = "symphony-ts";
    rev = "b7f99b85ac8385ffd45683afc56f19837f6b4111";
    hash = "sha256-B1F3HRoSi1eja8z/4NQcxDK8qUmq/4gPrX+SQwODO6A=";
  };

  # pnpm, not npm: the project declares packageManager pnpm@10 and ships only a
  # pnpm lockfile. The fetcher tolerates the minor version gap against nixpkgs'
  # pnpm by disabling manage-package-manager-versions.
  pnpmDeps = pnpm_10.fetchDeps {
    inherit (finalAttrs) pname version src;
    fetcherVersion = 2;
    hash = "sha256-Znzii1ADgghcCWOCHW2WFIvBLI5MThupnBlsBOcnBwA=";
  };

  nativeBuildInputs = [
    nodejs_22
    pnpm_10.configHook
    makeWrapper
  ];

  buildPhase = ''
    runHook preBuild
    pnpm build
    runHook postBuild
  '';

  # Only four runtime dependencies (graphql, liquidjs, yaml, zod), so pruning to
  # production leaves a small closure. The CLI is ESM and resolves its imports
  # relative to dist/, so dist and node_modules must stay siblings.
  installPhase = ''
    runHook preInstall

    pnpm prune --prod --ignore-scripts

    mkdir -p "$out/lib/symphony-ts"
    cp -r dist node_modules package.json "$out/lib/symphony-ts/"

    makeWrapper ${nodejs_22}/bin/node "$out/bin/symphony" \
      --add-flags "$out/lib/symphony-ts/dist/src/cli/main.js"

    runHook postInstall
  '';

  meta = {
    description = "Autonomous implementation runs driven by a tracker, with one isolated workspace per issue";
    homepage = "https://github.com/TarasKosh/symphony-ts";
    license = lib.licenses.asl20;
    mainProgram = "symphony";
    platforms = lib.platforms.linux;
  };
})

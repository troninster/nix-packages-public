{
  autoPatchelfHook,
  bash,
  bubblewrap,
  callPackage,
  cmake,
  fetchFromGitHub,
  gitMinimal,
  lib,
  makeDesktopItem,
  makeWrapper,
  ninja,
  nodejs_24,
  pnpm_10,
  python3,
  ripgrep,
  stdenv,
  stdenvNoCC,
  xdg-utils,
  ...
}:

let
  # The upstream workspace uses pnpm 11; the collection's older nixpkgs only
  # supplies pnpm 10. Use the matching upstream SQLite-aware dependency hooks.
  pnpmVersion = "11.7.0";
  pnpmHash = "sha256-3q+n7JihIYtqBHKJuS++I5XB4i00lbtxFlMBMhjuFe4=";
  pnpm11 = (pnpm_10.override {
    version = pnpmVersion;
    hash = pnpmHash;
    nodejs = nodejs_24;
    buildPackages = { pnpm_11 = pnpm11; };
  }).overrideAttrs {
    preConfigure = ''
      find dist/node_modules/@reflink -type f -name '*.node' -delete
      rm -r dist/vendor
    '';
    installPhase = ''
      runHook preInstall
      mkdir -p "$out/bin" "$out/libexec/pnpm"
      cp -R . "$out/libexec/pnpm/"
      ln -s "$out/libexec/pnpm/bin/pnpm.mjs" "$out/bin/pnpm"
      ln -s "$out/libexec/pnpm/bin/pnpx.mjs" "$out/bin/pnpx"
      runHook postInstall
    '';
  };
  pnpmFixupStateDb = (callPackage ./nixpkgs-pnpm11/pnpm-fixup-state-db/package.nix {
    nodejs = nodejs_24;
    pnpm = pnpm11;
  }).overrideAttrs {
    # The pinned npmConfigHook needs npmDeps exported to its Rust cache mapper.
    # Its older hook does not export structured attributes to subprocesses.
    __structuredAttrs = false;
  };
  pnpmHooks = callPackage ./nixpkgs-pnpm11/fetch-pnpm-deps {
    pnpm = pnpm11;
    pnpm-fixup-state-db = pnpmFixupStateDb;
    # The older platform schema lacks `node`; this package supports x86_64-linux.
    stdenvNoCC = stdenvNoCC // {
      targetPlatform = stdenvNoCC.targetPlatform // {
        node = { arch = "x64"; platform = "linux"; };
      };
    };
  };
in
stdenv.mkDerivation (finalAttrs: {
  pname = "deepseek-harness";
  version = "0.2.0-rc.2";

  src = fetchFromGitHub {
    owner = "deepseek-ai";
    repo = "deepseek-harness";
    rev = "639ed015397290b3745d163aafe02ffee4aa3f84";
    hash = "sha256-ZtO+bdoYbIkIgLTge5Eh7KYwTVh8FpFAAvx58dSY1PI=";
  };

  pnpmDepsHash = "sha256-U1oY4RI85iuBsZieVDqKyEAc9/CabQU+q6CgG8m4vRk=";
  pnpmInstallFlags = [ "--force=false" ];
  pnpmDeps = pnpmHooks.fetchPnpmDeps {
    inherit (finalAttrs) pname version src pnpmInstallFlags;
    fetcherVersion = 4;
    hash = finalAttrs.pnpmDepsHash;
    # Verify the original lockfile's supply-chain policy online in the FOD.
    # Only its native byHash proof belongs in the immutable snapshot; registry
    # metadata and other mutable cache contents stay in the temporary directory.
    prePnpmInstall = ''
      export pnpm_config_cache_dir="$(mktemp -d)"
      cp pnpm-lock.yaml "$pnpm_config_cache_dir/input-lock.yaml"
      cp pnpm-workspace.yaml "$pnpm_config_cache_dir/input-workspace.yaml"
    '';
    postInstall = ''
      cmp pnpm-lock.yaml "$pnpm_config_cache_dir/input-lock.yaml"
      cmp pnpm-workspace.yaml "$pnpm_config_cache_dir/input-workspace.yaml"
      ${nodejs_24}/bin/node ${./normalize-policy-cache.mjs} \
        "$pnpm_config_cache_dir/lockfile-verified.jsonl" "$PWD/pnpm-lock.yaml" \
        "$storePath/lockfile-verified.jsonl"
    '';
  };

  prePnpmInstall = ''
    if [ ! -s "$STORE_PATH/lockfile-verified.jsonl" ]; then
      echo "Missing pnpm online lockfile verification proof" >&2
      exit 1
    fi
    export pnpm_config_cache_dir="$(mktemp -d)"
    cp "$STORE_PATH/lockfile-verified.jsonl" "$pnpm_config_cache_dir/lockfile-verified.jsonl"
  '';

  nativeBuildInputs = [
    autoPatchelfHook
    cmake
    makeWrapper
    ninja
    nodejs_24
    pnpm11
    pnpmHooks.pnpmConfigHook
    python3
  ];
  buildInputs = [ stdenv.cc.cc.lib ];
  # CMake is used by Koffi, not as the workspace's top-level build system.
  dontUseCmakeConfigure = true;
  NODE_OPTIONS = "--max-old-space-size=3072";
  # Source archives have no Git metadata; use upstream's provenance override.
  DSH_CLIENT_COMMIT_HASH = finalAttrs.src.rev;

  # pnpm's hook installs the immutable lock closure without lifecycle scripts.
  # Rebuild the required native dependencies explicitly without registry access;
  # repository-only Git hook installation is deliberately not run.
  postPatch = ''
    substituteInPlace native/system/scripts/build.ts \
      --replace-fail "const headers = resolve(dirname(process.execPath), '../include/node')" \
        "const headers = '${lib.getDev nodejs_24}/include/node'"
  '';

  buildPhase = ''
    runHook preBuild

    export npm_config_nodedir=${lib.getDev nodejs_24}
    export npm_config_build_from_source=true
    export npm_config_jobs="$NIX_BUILD_CORES"
    export ELECTRON_SKIP_BINARY_DOWNLOAD=1
    export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

    pnpm rebuild esbuild

    ptyDirectory=$(node -p "require('path').dirname(require.resolve('node-pty/package.json', { paths: ['./packages/subprocess/subprocess-local'] }))")
    pushd "$ptyDirectory"
    node ${pnpm11}/libexec/pnpm/dist/node_modules/node-gyp/bin/node-gyp.js rebuild \
      --nodedir=${lib.getDev nodejs_24}
    popd

    koffiDirectory=$(node -p "require('path').dirname(require.resolve('koffi', { paths: ['./packages/subprocess/subprocess-local'] }))")
    pushd "$koffiDirectory"
    node cnoke.cjs -P . -D src/koffi --release
    popd

    # The module-loader addon and compiler bindings are locked npm dependencies.
    # They must load during the source build, before the final fixup phase.
    autoPatchelf node_modules

    pnpm run build
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    # Keep the upstream workspace topology: profile bundles, generated contracts,
    # client assets and native packages resolve through its workspace links.
    mkdir -p "$out/lib/deepseek-harness"
    cp -R apps packages vendor native node_modules package.json \
      pnpm-workspace.yaml pnpm-lock.yaml LICENSE "$out/lib/deepseek-harness/"

    makeWrapper ${nodejs_24}/bin/node "$out/bin/dsh" \
      --add-flags "$out/lib/deepseek-harness/apps/cli/lib/bin.js" \
      --prefix PATH : ${lib.makeBinPath [ bash bubblewrap gitMinimal pnpm11 ripgrep xdg-utils ]}

    mkdir -p "$out/share/applications"
    cp ${makeDesktopItem {
      name = "deepseek-harness";
      desktopName = "DeepSeek Harness";
      comment = "Launch the local DeepSeek Harness Web interface";
      exec = "dsh web";
      terminal = false;
      categories = [ "Development" ];
    }}/share/applications/deepseek-harness.desktop "$out/share/applications/"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export DSH_HOME="$TMPDIR/deepseek-harness-install-check"
    test "$("$out/bin/dsh" --version)" = "${finalAttrs.version}"
    "$out/bin/dsh" --help | grep -Fq -- '--profile'
    test -f "$out/lib/deepseek-harness/apps/web/dist/index.html"
    ${nodejs_24}/bin/node --input-type=module -e \
      "import { createRequire } from 'node:module'; const require = createRequire('$out/lib/deepseek-harness/packages/subprocess/subprocess-local/package.json'); require('node-pty'); require('koffi');"
    test ! -e "$DSH_HOME"
    ${nodejs_24}/bin/node ${./web-smoke.mjs} "$out/bin/dsh" "$TMPDIR"
    runHook postInstallCheck
  '';

  meta = {
    description = "DeepSeek's plugin-based agent harness with CLI and local Web interface";
    homepage = "https://github.com/deepseek-ai/deepseek-harness";
    changelog = "https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v${finalAttrs.version}";
    license = [ lib.licenses.mit lib.licenses.bsd3 ];
    mainProgram = "dsh";
    platforms = [ "x86_64-linux" ];
  };
})

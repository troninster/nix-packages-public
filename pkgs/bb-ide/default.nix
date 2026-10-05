{
  lib,
  stdenv,
  fetchFromGitHub,
  fetchurl,
  electron_39-bin,
  nodejs_24,
  pnpm_9,
  python3,
  makeWrapper,
  makeDesktopItem,
  copyDesktopItems,
  autoPatchelfHook,
  bash,
  gitMinimal,
  ...
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "bb-ide";
  version = "0.45.0";

  src = fetchFromGitHub {
    owner = "get-bb";
    repo = "bb";
    rev = "4147566cb00e7e12f4984d217c6be1518c63782f";
    hash = "sha256-L39Xz42+M9UEU5OXO90ZAImE9/t8Ob7J0YQrV/3Z0Wg=";
  };

  pnpmDepsHash = "sha256-M8jdPnVtkzpHmfvID5Hga00h4+YCTh6ZRfHAFuEPtEE=";
  electronVersion = "44.3.0";
  electronHash = "sha256-i0m5791zwPRn7cPBzVZ4OSw4TM8iTzT/VBefc24vOEs=";
  electronRuntime = electron_39-bin.overrideAttrs (old: {
    version = finalAttrs.electronVersion;
    src = fetchurl {
      url = "https://github.com/electron/electron/releases/download/v${finalAttrs.electronVersion}/electron-v${finalAttrs.electronVersion}-linux-x64.zip";
      hash = finalAttrs.electronHash;
    };
    # Electron 44 no longer ships separate ANGLE libraries.
    postFixup = lib.replaceStrings
      [ "# patch libANGLE" "$out/libexec/electron/lib*GL*" ]
      [
        "if compgen -G \"$out/libexec/electron/lib*GL*\" > /dev/null; then\n# patch libANGLE"
        "$out/libexec/electron/lib*GL*\nfi"
      ]
      old.postFixup;
  });

  pnpmWorkspaces = [
    "bb" "bb-app..." "@bb/desktop..." "@bb/app..." "@bb/server..."
    "@bb/host-daemon..." "@bb/cli..." "@bb/bundled-plugins..."
  ];
  pnpmInstallFlags = [ "--force=false" ];
  pnpmDeps = pnpm_9.fetchDeps {
    inherit (finalAttrs) pname version src pnpmWorkspaces pnpmInstallFlags;
    fetcherVersion = 2;
    hash = finalAttrs.pnpmDepsHash;
  };

  nativeBuildInputs = [
    nodejs_24 pnpm_9.configHook python3 makeWrapper copyDesktopItems autoPatchelfHook
  ];
  # Generated launchers spawn Node scripts directly; the host shebang hook
  # needs the runtime interpreter, not just its build-time counterpart.
  buildInputs = [ nodejs_24 stdenv.cc.cc.lib ];
  # The lock includes optional musl variants, never selected on this glibc host.
  # Keep all other missing libraries fatal, and smoke-test the active addons.
  autoPatchelfIgnoreMissingDeps = [ "libc.musl-x86_64.so.1" ];
  ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
  npm_config_nodedir = "${lib.getDev nodejs_24}";
  npm_config_build_from_source = "true";
  npm_config_jobs = "1";
  NODE_OPTIONS = "--max-old-space-size=3072";
  TURBO_TELEMETRY_DISABLED = "1";
  BB_DESKTOP_COMMIT = finalAttrs.src.rev;
  BB_DESKTOP_BUILD_DATE = "1970-01-01T00:00:00.000Z";

  buildPhase = ''
    runHook preBuild
    # Optional musl libvips has the same SONAME as glibc libvips. Keep its
    # package metadata/links, but exclude the unused ELF from library discovery.
    (
      shopt -s nullglob
      for library in node_modules/.pnpm/@img+sharp-libvips-linuxmusl-x64@*/node_modules/@img/sharp-libvips-linuxmusl-x64/lib/libvips-cpp.so.*; do
        if [ ! -f "$library" ] || [ -L "$library" ] || ! isELF "$library"; then
          echo "Unexpected optional musl libvips payload" >&2
          exit 1
        fi
        rm -- "$library"
      done
    )
    autoPatchelf node_modules
    pnpm --recursive rebuild better-sqlite3 node-pty fs-native-extensions @parcel/watcher esbuild
    pnpm exec turbo run build --filter=bb-app --filter=@bb/desktop --concurrency=1 --env-mode=loose
    runHook postBuild
  '';

  desktopItems = [ (makeDesktopItem {
    name = "bb-ide";
    desktopName = "BB IDE";
    genericName = "Agent IDE";
    exec = "bb-ide %U";
    icon = "bb-ide";
    categories = [ "Development" "IDE" ];
    terminal = false;
  }) ];

  installPhase = ''
    runHook preInstall
    # pnpm 9 deploy reinjects workspace dependencies and ignores the lockfile.
    # Preserve the already-built frozen closure and its relative workspace links.
    mkdir -p "$out/share/bb/runtime"
    cp -R apps packages plugins examples tests node_modules package.json pnpm-workspace.yaml pnpm-lock.yaml LICENSE \
      "$out/share/bb/runtime/"
    mkdir -p "$out/share/bb/desktop/node_modules" "$out/share/icons/hicolor/512x512/apps"
    cp -r apps/desktop/dist apps/desktop/assets apps/desktop/package.json "$out/share/bb/desktop/"
    ln -s ../../runtime/packages/bb-app "$out/share/bb/desktop/node_modules/bb-app"
    cp apps/desktop/assets/icon.png "$out/share/icons/hicolor/512x512/apps/bb-ide.png"

    for name in bb bb-app bb-server bb-host-daemon; do
      makeWrapper ${nodejs_24}/bin/node "$out/bin/$name" \
        --add-flags "$out/share/bb/runtime/packages/bb-app/dist/$name.js" \
        --set NODE_ENV production --set BB_TELEMETRY false \
        --prefix PATH : ${lib.makeBinPath [ nodejs_24 bash gitMinimal ]}
    done
    makeWrapper ${finalAttrs.electronRuntime}/bin/electron "$out/bin/bb-ide" \
      --add-flags "$out/share/bb/desktop" \
      --set BB_DESKTOP_NODE_EXEC_PATH ${nodejs_24}/bin/node \
      --set NODE_ENV production --set BB_TELEMETRY false \
      --set BB_DESKTOP_AUTO_UPDATE 0 --set BB_DESKTOP_VERSION_CHECK 0 \
      --prefix PATH : ${lib.makeBinPath [ nodejs_24 bash gitMinimal ]}
    runHook postInstall
  '';

  preFixup = ''
    # Pre-build autoPatchelf resolves bundled libraries inside the source tree.
    # Relocate those RPATH entries with the copied workspace before the normal
    # shrink/check hooks; store paths and $ORIGIN entries remain unchanged.
    while IFS= read -r -d "" binary; do
      isELF "$binary" || continue
      rpath=$(patchelf --print-rpath "$binary" 2>/dev/null) || continue
      relocatedRpath="''${rpath//"$PWD/"/"$out/share/bb/runtime/"}"
      if [ "$rpath" != "$relocatedRpath" ]; then
        patchelf --set-rpath "$relocatedRpath" "$binary"
      fi
    done < <(find "$out/share/bb/runtime" -type f -print0)
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export BB_DATA_DIR="$TMPDIR/bb-smoke"
    "$out/bin/bb" --help > "$TMPDIR/bb-help"
    grep -Fq 'bb' "$TMPDIR/bb-help"
    ELECTRON_RUN_AS_NODE=1 ${finalAttrs.electronRuntime}/bin/electron -e \
      'if (process.versions.electron !== "${finalAttrs.electronVersion}") process.exit(1)'
    ${nodejs_24}/bin/node -e '
      const load = require("node:module").createRequire(process.argv[1]);
      const Database = load("better-sqlite3");
      const db = new Database(":memory:");
      if (db.prepare("SELECT 1 AS value").get().value !== 1) process.exit(1);
      db.close();
      load("node-pty");
      load("@parcel/watcher");
      load("fs-native-extensions");
      const loadSharp = require("node:module").createRequire(process.argv[2]);
      loadSharp("sharp");
    ' "$out/share/bb/runtime/packages/bb-app/package.json" \
      "$out/share/bb/runtime/apps/app/package.json"
    test -f "$out/share/bb/runtime/packages/bb-app/app/dist/index.html"
    test -f "$out/share/bb/desktop/dist/preload.cjs"
    test -f "$out/share/bb/desktop/dist/bb-app-bridge.mjs"
    runHook postInstallCheck
  '';

  meta = {
    description = "Source-built BB agent IDE with Electron desktop and CLI";
    homepage = "https://github.com/get-bb/bb";
    license = lib.licenses.mit;
    mainProgram = "bb-ide";
    platforms = [ "x86_64-linux" ];
  };
})

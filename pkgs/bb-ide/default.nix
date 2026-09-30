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
  version = "0.44.0";

  src = fetchFromGitHub {
    owner = "get-bb";
    repo = "bb";
    rev = "ea3b328f366a61e00f115399f6da9c2ebe7ed242";
    hash = "sha256-VSjlOYnNsamHaSlLMuqKM+seA0gA2llGJGA4P+mGHW8=";
  };

  pnpmDepsHash = lib.fakeHash;
  electronVersion = "44.3.0";
  electronHash = "sha256-i0m5791zwPRn7cPBzVZ4OSw4TM8iTzT/VBefc24vOEs=";
  electronRuntime = electron_39-bin.overrideAttrs (_: {
    version = finalAttrs.electronVersion;
    src = fetchurl {
      url = "https://github.com/electron/electron/releases/download/v${finalAttrs.electronVersion}/electron-v${finalAttrs.electronVersion}-linux-x64.zip";
      hash = finalAttrs.electronHash;
    };
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
  buildInputs = [ stdenv.cc.cc.lib ];
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
    autoPatchelf node_modules
    pnpm --recursive --workspace-concurrency=1 rebuild better-sqlite3 node-pty fs-native-extensions @parcel/watcher esbuild
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
    pnpm --filter bb-app deploy --prod --offline --ignore-scripts "$out/share/bb/runtime"
    pushd "$out/share/bb/runtime"
    pnpm rebuild better-sqlite3 node-pty fs-native-extensions @parcel/watcher
    popd
    mkdir -p "$out/share/bb/desktop/node_modules" "$out/share/icons/hicolor/512x512/apps"
    cp -r apps/desktop/dist apps/desktop/assets apps/desktop/package.json "$out/share/bb/desktop/"
    ln -s ../../runtime "$out/share/bb/desktop/node_modules/bb-app"
    cp apps/desktop/assets/icon.png "$out/share/icons/hicolor/512x512/apps/bb-ide.png"

    for name in bb bb-app bb-server bb-host-daemon; do
      makeWrapper ${nodejs_24}/bin/node "$out/bin/$name" \
        --add-flags "$out/share/bb/runtime/dist/$name.js" \
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
    ' "$out/share/bb/runtime/package.json"
    test -f "$out/share/bb/runtime/app/dist/index.html"
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

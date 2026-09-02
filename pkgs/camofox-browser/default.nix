{
  lib,
  stdenv,
  buildNpmPackage,
  fetchFromGitHub,
  fetchurl,
  nodejs,
  python3,
  makeWrapper,
  autoPatchelfHook,
  unzip,
  yt-dlp,
  xorg,
  tigervnc,
  novnc,
  python3Packages,
  bash,
  which,
  alsa-lib,
  atk,
  cairo,
  cups,
  dbus,
  expat,
  fontconfig,
  freetype,
  glib,
  gtk3,
  libGL,
  libdrm,
  libnotify,
  libpulseaudio,
  libuuid,
  libxkbcommon,
  mesa,
  nspr,
  nss,
  pango,
  udev,
  zlib,
  ...
}:

let
  camofoxBrowserVersion = "1.11.2";
  camofoxBrowserRev = "ce3a3b085aacba73eb8de6c51733c19fb13bfae4";
  camofoxBrowserHash = "sha256-4ca/zUNe/3h4H9SbyP/1DDPM8zlWXmb+SrPF1qzgy9c=";
  camofoxBrowserNpmDepsHash = "sha256-FDecut8Gsy2pHrHRzpGf1Xw1Uvzjtaoq6JUhAyTUQsA=";
  camoufoxEngineReleaseTag = "v152.0.4-beta.27";
  camoufoxEngineVersion = "152.0.4-beta.27";
  camoufoxEngineHash = "sha256-xtJLGBltj6vPB06dU996AmMjyy4wsl/TYwva4CXBpEs=";
  camoufoxEngineMetadata =
    let
      match = builtins.match "([0-9.]+)-(.+)" camoufoxEngineVersion;
    in
    if match == null then
      {
        version = camoufoxEngineVersion;
        release = "stable";
      }
    else
      {
        version = builtins.elemAt match 0;
        release = builtins.elemAt match 1;
      };
in
buildNpmPackage rec {
  pname = "camofox-browser";
  version = camofoxBrowserVersion;

  src = fetchFromGitHub {
    owner = "jo-inc";
    repo = "camofox-browser";
    rev = camofoxBrowserRev;
    hash = camofoxBrowserHash;
  };

  camoufoxEngine = fetchurl {
    url = "https://github.com/daijro/camoufox/releases/download/${camoufoxEngineReleaseTag}/camoufox-${camoufoxEngineVersion}-lin.x86_64.zip";
    hash = camoufoxEngineHash;
  };

  npmDepsHash = camofoxBrowserNpmDepsHash;
  postPatch = ''
    ${python3}/bin/python ${../../scripts/repair-camofox-package-lock.py} package-lock.json package.json
  '';
  # npmConfigHook installs with --ignore-scripts, then runs npm rebuild. The
  # impit package's rebuild only invokes a package-manager guard via npx, which
  # is unavailable in Nix's offline npm cache. Rebuild only the native module the
  # server actually needs on Linux.
  npmRebuildFlags = [ "better-sqlite3" ];

  nativeBuildInputs = [
    makeWrapper
    python3
    autoPatchelfHook
    unzip
  ];

  buildInputs = [
    alsa-lib
    atk
    cairo
    cups
    dbus
    expat
    fontconfig
    freetype
    glib
    gtk3
    libGL
    libdrm
    libnotify
    libpulseaudio
    libuuid
    libxkbcommon
    mesa
    nspr
    nss
    pango
    stdenv.cc.cc.lib
    udev
    xorg.libX11
    xorg.libXScrnSaver
    xorg.libXcomposite
    xorg.libXcursor
    xorg.libXdamage
    xorg.libXext
    xorg.libXfixes
    xorg.libXi
    xorg.libXrandr
    xorg.libXrender
    xorg.libXtst
    xorg.libxcb
    zlib
  ];

  autoPatchelfIgnoreMissingDeps = [
    # impit ships both GNU and musl native modules; Node selects the GNU one on NixOS.
    "libc.musl-x86_64.so.1"
  ];

  # Firefox/Camoufox launcher binaries dlopen engine libraries from their own
  # directory. Preserve sibling engine library lookup after autoPatchelf rewrites
  # RPATHs, otherwise camoufox-bin segfaults very early on NixOS.
  appendRunpaths = [ "$ORIGIN" ];

  # Do not let autoPatchelf recurse through bundled Firefox/Camoufox libraries:
  # rewriting those .so files can make libnspr4.so segfault during dlopen. We
  # manually patch only Node native modules and the two launcher executables in
  # postFixup below, then provide Firefox's system library deps via wrappers.
  dontAutoPatchelf = true;

  runtimeDeps = [
    nodejs
    yt-dlp
    xorg.xvfb
    tigervnc
    novnc
    python3Packages.websockify
    bash
    which
  ];

  # Keep the browser engine offline/reproducible: the Camoufox engine is fetched
  # above as a fixed-output Nix input and wired into camoufox-js via
  # CAMOUFOX_INSTALL_DIR. Do not pass --ignore-scripts globally, because native
  # Node modules such as better-sqlite3 need their install hooks to build .node
  # bindings for the Nix-provided Node ABI.
  dontNpmBuild = true;

  buildPhase = ''
    runHook preBuild
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/camofox-browser $out/lib/camoufox-engine
    cp -R . $out/lib/camofox-browser
    # unzip exits non-zero on a few non-Linux font filename warnings in this
    # archive; the needed Linux payload is still extracted correctly.
    unzip -q $camoufoxEngine -d $out/lib/camoufox-engine || true
    chmod -R u+rwX,go+rX $out/lib/camoufox-engine
    echo ${lib.escapeShellArg (builtins.toJSON camoufoxEngineMetadata)} > $out/lib/camoufox-engine/version.json

    # Older camoufox-js releases hard-code ~/.cache/camoufox; newer releases
    # support CAMOUFOX_INSTALL_DIR directly. Accept those two known source
    # shapes and stop on any other layout so an upstream change cannot silently
    # drop the Nix-store engine path.
    camoufoxPkgman=$out/lib/camofox-browser/node_modules/camoufox-js/dist/pkgman.js
    if grep -Fq 'export const INSTALL_DIR = process.env.CAMOUFOX_INSTALL_DIR' "$camoufoxPkgman"; then
      echo "camofox-browser: upstream camoufox-js supports CAMOUFOX_INSTALL_DIR"
    elif grep -Fq 'export const INSTALL_DIR = userCacheDir("camoufox");' "$camoufoxPkgman"; then
      substituteInPlace "$camoufoxPkgman" \
        --replace-fail 'export const INSTALL_DIR = userCacheDir("camoufox");' \
                       'export const INSTALL_DIR = process.env.CAMOUFOX_INSTALL_DIR || userCacheDir("camoufox");'
    else
      echo "camofox-browser: unsupported camoufox-js INSTALL_DIR layout" >&2
      exit 1
    fi

    substituteInPlace "$camoufoxPkgman" \
      --replace-fail 'export function camoufoxPath(downloadIfMissing = true) {
    // Ensure the directory exists and is not empty' \
                     'export function camoufoxPath(downloadIfMissing = true) {
    if (process.env.CAMOUFOX_INSTALL_DIR && fs.existsSync(INSTALL_DIR) && fs.readdirSync(INSTALL_DIR).length > 0) {
        return INSTALL_DIR;
    }
    // Ensure the directory exists and is not empty'

    # Default camoufox-js addons are downloaded into CAMOUFOX_INSTALL_DIR at
    # launch time. In Nix that path is the read-only browser engine in /nix/store,
    # so skip the default UBO addon rather than letting startup try to mutate the
    # store and log EROFS errors. Custom addons can still be passed explicitly.
    camofoxServer=$out/lib/camofox-browser/server.js
    legacyDefaultAddonAnchor="        virtual_display: vdDisplay,
      });"
    if grep -Fqx "        exclude_addons: CONFIG.disableDefaultAddons ? ['UBO'] : undefined," "$camofoxServer"; then
      echo "camofox-browser: upstream supports disabling default addons"
    elif [[ "$(cat "$camofoxServer")" == *"$legacyDefaultAddonAnchor"* ]]; then
      substituteInPlace "$camofoxServer" \
        --replace-fail "$legacyDefaultAddonAnchor" \
                       "        virtual_display: vdDisplay,
        exclude_addons: ['UBO'],
      });"
    else
      echo "camofox-browser: unsupported default-addon layout" >&2
      exit 1
    fi

    # The upstream tab reaper runs every minute and closes sessions with zero
    # tabs. On slower NixOS/Camoufox starts, POST /tabs may create a session and
    # then spend 15-25s in browserContext.newPage() before adding the first tab;
    # if the reaper ticks during that window it closes the context under the
    # request and /tabs returns "Target page, context or browser has been
    # closed". Give fresh empty sessions a short grace period.
    nativeSessionGraceAnchor='    if (session.tabGroups.size === 0 && !hasActivePageLeases(session)) {'
    legacySessionGraceAnchor='    if (session.tabGroups.size === 0) {'
    if grep -Fqx "$nativeSessionGraceAnchor" "$camofoxServer"; then
      substituteInPlace "$camofoxServer" \
        --replace-fail "$nativeSessionGraceAnchor" \
                       '    if (session.tabGroups.size === 0 && !hasActivePageLeases(session) && now - session.lastAccess > 120000) {'
    elif grep -Fqx "$legacySessionGraceAnchor" "$camofoxServer"; then
      substituteInPlace "$camofoxServer" \
        --replace-fail "$legacySessionGraceAnchor" \
                       '    if (session.tabGroups.size === 0 && now - session.lastAccess > 120000) {'
    else
      echo "camofox-browser: unsupported empty-session grace layout" >&2
      exit 1
    fi

    # Cold Camoufox startup on this NixOS profile can exceed the upstream 30s
    # generic handler timeout. Give request-scoped routes enough budget for a
    # browser launch plus the actual operation; proxy rotation keeps its larger
    # upstream floor.
    substituteInPlace $out/lib/camofox-browser/server.js \
      --replace-fail "function requestTimeoutMs(baseMs = HANDLER_TIMEOUT_MS) {
  return proxyPool?.canRotateSessions ? Math.max(baseMs, 180000) : baseMs;
}" \
                     "function requestTimeoutMs(baseMs = HANDLER_TIMEOUT_MS) {
  const localFloorMs = 120000;
  const proxyFloorMs = 180000;
  return proxyPool?.canRotateSessions ? Math.max(baseMs, proxyFloorMs) : Math.max(baseMs, localFloorMs);
}"

    # The upstream cleanup intervals used to call scheduleBrowserIdleShutdown()
    # every minute while sessions.size == 0. Older versions cleared and
    # recreated the timer each time, so the 5-minute browser idle shutdown never
    # fired. Newer upstream versions are already idempotent; keep the local patch
    # only for old reset-on-call implementations.
    if grep -Fq "if (browserIdleTimer || sessions.size > 0 || !browser) return;" \
      $out/lib/camofox-browser/server.js; then
      echo "camofox-browser: upstream idle shutdown timer is already idempotent"
    else
      substituteInPlace $out/lib/camofox-browser/server.js \
        --replace-fail "function scheduleBrowserIdleShutdown() {
  clearBrowserIdleTimer();
  if (sessions.size === 0 && browser) {
    browserIdleTimer = setTimeout(async () => {
      if (sessions.size === 0 && browser) {
        log('info', 'browser idle shutdown (no sessions)');
        await closeBrowserFully('idle_shutdown');
      }
    }, BROWSER_IDLE_TIMEOUT_MS);
  }
}" \
                       "function scheduleBrowserIdleShutdown() {
  if (browserIdleTimer) return;
  if (sessions.size === 0 && browser) {
    browserIdleTimer = setTimeout(async () => {
      browserIdleTimer = null;
      if (sessions.size === 0 && browser) {
        log('info', 'browser idle shutdown (no sessions)');
        await closeBrowserFully('idle_shutdown');
      }
    }, BROWSER_IDLE_TIMEOUT_MS);
  }
}"
    fi

    # Keep health probes from becoming their own background workload: do not
    # probe an idle browser with no sessions/tabs, and do not let async probe
    # attempts overlap if Camoufox/Juggler is wedged.
    substituteInPlace $out/lib/camofox-browser/server.js \
      --replace-fail "const healthState = {
  consecutiveNavFailures: 0,
  lastSuccessfulNav: Date.now(),
  isRecovering: false,
  activeOps: 0,
};" \
                     "const healthState = {
  consecutiveNavFailures: 0,
  lastSuccessfulNav: Date.now(),
  isRecovering: false,
  activeOps: 0,
};
let activeHealthProbeInFlight = false;"

    # A browser relaunched after idle shutdown can inherit a stale
    # lastSuccessfulNav value. Reset it on launch so the first real operation
    # after a long idle window does not race the active health probe.
    substituteInPlace $out/lib/camofox-browser/server.js \
      --replace-fail "      pluginEvents.emit('browser:launched', { browser, display: vdDisplay });" \
                     "      pluginEvents.emit('browser:launched', { browser, display: vdDisplay });
      healthState.consecutiveNavFailures = 0;
      healthState.lastSuccessfulNav = Date.now();"

    substituteInPlace $out/lib/camofox-browser/server.js \
      --replace-fail "// Active health probe -- detect hung browser even when isConnected() lies
setInterval(async () => {
  if (!browser || healthState.isRecovering) return;
  const timeSinceSuccess = Date.now() - healthState.lastSuccessfulNav;
  // Skip probe if operations are in flight AND last success was recent.
  // If it's been >120s since any successful operation, probe anyway --
  // active ops are likely stuck on a frozen browser and will time out eventually.
  if (healthState.activeOps > 0 && timeSinceSuccess < 120000) {
    log('info', 'health probe skipped, operations active', { activeOps: healthState.activeOps });
    return;
  }
  if (timeSinceSuccess < 120000) return;
${"  "}
  if (healthState.activeOps > 0) {
    log('warn', 'health probe forced despite active ops', { activeOps: healthState.activeOps, timeSinceSuccessMs: timeSinceSuccess });
  }
${"  "}
  let testContext;
  try {
    testContext = await browser.newContext();
    const page = await testContext.newPage();
    await page.goto('about:blank', { timeout: 5000 });
    await page.close();
    await testContext.close();
    healthState.lastSuccessfulNav = Date.now();
  } catch (err) {
    failuresTotal.labels('health_probe', 'internal').inc();
    log('warn', 'health probe failed', { error: err.message, timeSinceSuccessMs: timeSinceSuccess });
    if (testContext) await testContext.close().catch(() => {});
    restartBrowser('health probe failed').catch(() => {});
  }
}, 60_000);" \
                     "// Active health probe -- detect hung browser even when isConnected() lies
setInterval(async () => {
  if (!browser || healthState.isRecovering || activeHealthProbeInFlight) return;
  if (sessions.size === 0 && getTotalTabCount() === 0) return;
  const timeSinceSuccess = Date.now() - healthState.lastSuccessfulNav;
  // Skip probe if operations are in flight AND last success was recent.
  // If it's been >120s since any successful operation, probe anyway --
  // active ops are likely stuck on a frozen browser and will time out eventually.
  if (healthState.activeOps > 0 && timeSinceSuccess < 120000) {
    log('info', 'health probe skipped, operations active', { activeOps: healthState.activeOps });
    return;
  }
  if (timeSinceSuccess < 120000) return;

  if (healthState.activeOps > 0) {
    log('warn', 'health probe forced despite active ops', { activeOps: healthState.activeOps, timeSinceSuccessMs: timeSinceSuccess });
  }

  activeHealthProbeInFlight = true;
  let testContext;
  try {
    testContext = await browser.newContext();
    const page = await testContext.newPage();
    await page.goto('about:blank', { timeout: 5000 });
    await page.close().catch(() => {});
    await testContext.close();
    testContext = null;
    healthState.consecutiveNavFailures = 0;
    healthState.lastSuccessfulNav = Date.now();
  } catch (err) {
    failuresTotal.labels('health_probe', 'internal').inc();
    log('warn', 'health probe failed', { error: err.message, timeSinceSuccessMs: timeSinceSuccess });
    if (sessions.size > 0 || getTotalTabCount() > 0 || healthState.activeOps > 0) {
      restartBrowser('health probe failed').catch(() => {});
    } else {
      scheduleBrowserIdleShutdown();
    }
  } finally {
    if (testContext) await testContext.close().catch(() => {});
    activeHealthProbeInFlight = false;
  }
}, 60_000);"

    makeWrapper ${nodejs}/bin/node $out/bin/camofox-browser \
      --add-flags "$out/lib/camofox-browser/server.js" \
      --set CAMOUFOX_INSTALL_DIR "$out/lib/camoufox-engine" \
      --set CAMOFOX_DISABLE_DEFAULT_ADDONS 1 \
      --prefix LD_LIBRARY_PATH : "$out/lib/camoufox-engine:${lib.makeLibraryPath buildInputs}" \
      --prefix PATH : ${lib.makeBinPath runtimeDeps}

    makeWrapper ${nodejs}/bin/node $out/bin/camoufox \
      --add-flags "$out/lib/camofox-browser/node_modules/camoufox-js/dist/__main__.js" \
      --set CAMOUFOX_INSTALL_DIR "$out/lib/camoufox-engine" \
      --prefix LD_LIBRARY_PATH : "$out/lib/camoufox-engine:${lib.makeLibraryPath buildInputs}" \
      --prefix PATH : ${lib.makeBinPath runtimeDeps}

    runHook postInstall
  '';

  postFixup = ''
    # Patch Node native modules, but leave bundled Firefox/Camoufox libraries as
    # shipped upstream. Patching the full engine recursively causes early
    # SIGSEGV in libnspr4.so initialization on NixOS.
    autoPatchelf -- $out/lib/camofox-browser/node_modules

    # Patch only the launcher binaries so NixOS can execute them. Their dlopened
    # engine libraries are resolved from LD_LIBRARY_PATH set by the wrappers.
    autoPatchelf --no-recurse -- \
      $out/lib/camoufox-engine/camoufox \
      $out/lib/camoufox-engine/camoufox-bin
  '';

  meta = {
    description = "Anti-detection Camoufox browser REST API server for AI agents";
    homepage = "https://github.com/jo-inc/camofox-browser";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "camofox-browser";
  };
}

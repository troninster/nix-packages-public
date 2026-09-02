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
  camofoxBrowserVersion = "1.14.0";
  camofoxBrowserRev = "e5a36f5cd0332fde6597de474329a308a53a0716";
  camofoxBrowserHash = "sha256-POVwAiVoScS5c1QMZslz1wbfWttYdeQEy2msxoVt+uk=";
  camofoxBrowserNpmDepsHash = "sha256-W+8NKDqwBY6vJtgmrY5rYqDd4sxzBRbk65w9krwTK5g=";
  camoufoxEngineReleaseTag = "v152.0.4-beta.30";
  camoufoxEngineVersion = "152.0.4-beta.30";
  camoufoxEngineHash = "sha256-VyDUW4lM4XcFQ94CTG8Q1RSzi+Vg+i3DIms9hYbK9nI=";
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

    camoufoxPkgman=$out/lib/camofox-browser/node_modules/camoufox-js/dist/pkgman.js
    camofoxServer=$out/lib/camofox-browser/server.js
    # Classify every supported source layout before either file is changed.
    # Unknown or duplicate anchors are reported together and stop the build;
    # the apply phase repeats each exact-count guard before atomic file writes.
    ${python3}/bin/python ${../../scripts/patch-camofox-browser.py} \
      "$camoufoxPkgman" "$camofoxServer"

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

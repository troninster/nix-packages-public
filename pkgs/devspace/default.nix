{
  bash,
  buildNpmPackage,
  fetchFromGitHub,
  gitMinimal,
  lib,
  makeWrapper,
  nodejs_22,
  patchelf,
  stdenv,
  ...
}:

let
  buildNpmPackageNode22 = buildNpmPackage.override {
    nodejs = nodejs_22;
  };
in
buildNpmPackageNode22 rec {
  pname = "devspace";
  version = "1.0.7";

  src = fetchFromGitHub {
    owner = "Waishnav";
    repo = "devspace";
    rev = "b5b4ab62a8718e1186aef815538741d9402f92ba";
    hash = "sha256-SRcPaJF/0sTWhBCvkDNc2zYL42ItSISbA9pEBquUXc8=";
  };

  npmDepsHash = "sha256-fwrnMPIfjDNxFe4jcbKkh3TNgkqfYzyAJRVSqunZ7dg=";
  # npm 11 compacts cache indexes while reading them. The fixed-output Nix
  # cache is immutable, so give npm a build-local writable copy.
  makeCacheWritable = true;

  # Upstream's v1.0.7 lockfile omits integrity fields for three nested Pi
  # packages. Add the registry-published SRI values so Nix can prefetch the
  # otherwise fully pinned npm dependency graph.
  postPatch = ''
    substituteInPlace package-lock.json \
      --replace-fail \
        '"resolved": "https://registry.npmjs.org/@earendil-works/pi-agent-core/-/pi-agent-core-0.80.3.tgz",' \
        '"resolved": "https://registry.npmjs.org/@earendil-works/pi-agent-core/-/pi-agent-core-0.80.3.tgz", "integrity": "sha512-3qw0/GeRQBU/nlGjDe5Yb7ePKTmoxefx2YxyKMFAviFUMXpFexBG/hS7mBtwFahFvzrrTPPoRT6sFIDjwoDWPQ==",' \
      --replace-fail \
        '"resolved": "https://registry.npmjs.org/@earendil-works/pi-ai/-/pi-ai-0.80.3.tgz",' \
        '"resolved": "https://registry.npmjs.org/@earendil-works/pi-ai/-/pi-ai-0.80.3.tgz", "integrity": "sha512-jPZLMeGL5kkMSEAwAklfXTMHqZvfhsJtCCpKGIr5Duk7mc0n4skjB1dugk7y0z3z8ZHIUCmPAWHdyDqgUz5vdA==",' \
      --replace-fail \
        '"resolved": "https://registry.npmjs.org/@earendil-works/pi-tui/-/pi-tui-0.80.3.tgz",' \
        '"resolved": "https://registry.npmjs.org/@earendil-works/pi-tui/-/pi-tui-0.80.3.tgz", "integrity": "sha512-2BJI6qwRQfnM0Q7seL1+SbacU/jRRjBnN7Hu3n9BjAn7/s5FaBNnvdD1qBQYRsFTHfjqMaDsjYqanPyqwXj99w==",'
  '';

  nativeBuildInputs = [
    makeWrapper
    patchelf
  ];

  buildInputs = [
    stdenv.cc.cc.lib
  ];

  installPhase = ''
    runHook preInstall

    npm prune --omit=dev --ignore-scripts

    mkdir -p "$out/lib/devspace"
    cp -R dist node_modules package.json "$out/lib/devspace/"

    runtimeRpath=${lib.escapeShellArg (lib.makeLibraryPath [ stdenv.cc.cc.lib ])}
    for addon in \
      "$out/lib/devspace/node_modules/better-sqlite3/build/Release/better_sqlite3.node" \
      "$out/lib/devspace/node_modules/node-pty/build/Release/pty.node"; do
      test -f "$addon"
      patchelf --set-rpath "$runtimeRpath" "$addon"
    done

    makeWrapper ${nodejs_22}/bin/node "$out/bin/devspace" \
      --add-flags "$out/lib/devspace/dist/cli.js" \
      --prefix PATH : ${lib.makeBinPath [ bash gitMinimal ]}

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    export HOME="$TMPDIR/home"
    export DEVSPACE_CONFIG_DIR="$TMPDIR/config"
    mkdir -p "$HOME" "$DEVSPACE_CONFIG_DIR"

    test "$("$out/bin/devspace" --version)" = "${version}"
    "$out/bin/devspace" --help | grep -Fq "devspace doctor"
    "$out/bin/devspace" doctor | grep -Fq "SQLite native dependency: ok"
    ${nodejs_22}/bin/node -e \
      "require('$out/lib/devspace/node_modules/node-pty')"
    test ! -e "$HOME/.devspace"

    runHook postInstallCheck
  '';

  meta = {
    description = "Secure local coding workspace exposed through an MCP server";
    homepage = "https://github.com/Waishnav/devspace";
    changelog = "https://github.com/Waishnav/devspace/releases/tag/v${version}";
    license = lib.licenses.mit;
    mainProgram = "devspace";
    platforms = lib.platforms.linux;
  };
}

{
  lib,
  buildNpmPackage,
  fetchFromGitHub,
  makeWrapper,
  nodejs_22,
  ...
}:

# Vendored upstream: pinned FreeLLMAPI source with no local source changes.
buildNpmPackage rec {
  pname = "freellmapi";
  version = "0.13.6";

  src = fetchFromGitHub {
    owner = "tashfeenahmed";
    repo = "freellmapi";
    rev = "ffef850fe8553b03f89e2f76be4cb4da2b709378";
    hash = "sha256-l4l44Q7aw2oKz09HSGVa+w+3f9P9k0UJcC/xhQYvXWI=";
  };

  nodejs = nodejs_22;
  npmDepsHash = "sha256-7L32KsKeq+oI/tvadWiBpbHvHLqWZ4hSvUnInDOfs9E=";

  # Upstream's root build now includes a CLI workspace. This package exposes
  # the existing server/client runtime only, so keep those two builds explicit.
  buildPhase = ''
    runHook preBuild

    npm run build:server
    npm run build -w client

    runHook postBuild
  '';

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    npm prune --omit=dev --ignore-scripts

    mkdir -p $out/lib/freellmapi/server $out/lib/freellmapi/client
    cp -r node_modules $out/lib/freellmapi/node_modules
    # npm creates this workspace link for the omitted cli package; remove only
    # that broken self-link after copying the server/client dependencies.
    rm -f $out/lib/freellmapi/node_modules/freellmapi
    test ! -e $out/lib/freellmapi/node_modules/freellmapi
    cp -r server/dist server/package.json server/node_modules $out/lib/freellmapi/server/
    cp -r client/dist client/package.json $out/lib/freellmapi/client/
    cp -r shared $out/lib/freellmapi/shared

    makeWrapper ${nodejs_22}/bin/node $out/bin/freellmapi \
      --add-flags "$out/lib/freellmapi/server/dist/index.js"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    (cd $out/lib/freellmapi/server && \
      ${nodejs_22}/bin/node --input-type=module --eval \
        "await import('ajv/dist/2020.js')")

    runHook postInstallCheck
  '';

  meta = {
    description = "OpenAI-compatible proxy for free-tier LLM providers";
    homepage = "https://github.com/tashfeenahmed/freellmapi";
    changelog = "https://github.com/tashfeenahmed/freellmapi/releases/tag/v${version}";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "freellmapi";
  };
}

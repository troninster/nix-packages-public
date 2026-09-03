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
  version = "0.9.5";

  src = fetchFromGitHub {
    owner = "tashfeenahmed";
    repo = "freellmapi";
    rev = "aa28db9d3865049f605b6cdb4a404c8c87ca3f7e";
    hash = "sha256-VFoQmjfZ9OEjCgV3YCn/9Idl4mrYSd++5hNeUaST8aY=";
  };

  nodejs = nodejs_22;
  npmDepsHash = "sha256-8bgGKVICPmKAudvT9e3w/jJgwVnh9aV/sBu80AavN0g=";

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

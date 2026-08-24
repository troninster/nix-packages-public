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
  version = "0.8.7";

  src = fetchFromGitHub {
    owner = "tashfeenahmed";
    repo = "freellmapi";
    rev = "4774cf02a4e6c984e17298afbe25e7e61203c3ca";
    hash = "sha256-wUhWnWIGG1fgFkC71o5cYGfi9HaZsTsC9DnfMC8ZteQ=";
  };

  nodejs = nodejs_22;
  npmDepsHash = "sha256-k03n0DD7EIaCI1k6nHo1i76Ey9IqgXizNVHilWk6MBU=";

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
    cp -r server/dist server/package.json $out/lib/freellmapi/server/
    cp -r client/dist client/package.json $out/lib/freellmapi/client/
    cp -r shared $out/lib/freellmapi/shared

    makeWrapper ${nodejs_22}/bin/node $out/bin/freellmapi \
      --add-flags "$out/lib/freellmapi/server/dist/index.js"

    runHook postInstall
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

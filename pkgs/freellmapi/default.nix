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
  version = "0.5.0";

  src = fetchFromGitHub {
    owner = "tashfeenahmed";
    repo = "freellmapi";
    rev = "4ba015909289ebfef0c2d0a186b739669099023b";
    hash = "sha256-slCdFD4yEV2EIEPk9WLg1z8C0ZQzuWKIHIBYAKwAqvI=";
  };

  nodejs = nodejs_22;
  npmDepsHash = "sha256-NkawMnWujidvm1jXYyr5SQCa8Dr6oGpbBY1YnCMFJ+c=";

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

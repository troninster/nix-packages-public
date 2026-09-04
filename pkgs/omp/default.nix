{
  autoPatchelfHook,
  fetchurl,
  lib,
  stdenv,
  ...
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "omp";
  version = "18.1.10";

  src = fetchurl {
    url = "https://github.com/can1357/oh-my-pi/releases/download/v${finalAttrs.version}/omp-linux-x64";
    hash = "sha256-6R1VmO5H4dQJn9hobcn2HJt1Xy6gd9Xxd0q6EHIyH54=";
  };

  dontUnpack = true;
  # Bun's compiled payload is appended to the ELF; stripping leaves only the
  # Bun runtime and silently turns `omp` into `bun`.
  dontStrip = true;
  nativeBuildInputs = [ autoPatchelfHook ];

  installPhase = ''
    runHook preInstall
    install -Dm755 "$src" "$out/bin/omp"
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    HOME="$TMPDIR" "$out/bin/omp" --smoke-test | grep -q "smoke-test: ok"
    runHook postInstallCheck
  '';

  meta = {
    description = "Terminal-based coding agent with multi-model support";
    homepage = "https://omp.sh";
    changelog = "https://github.com/can1357/oh-my-pi/releases/tag/v${finalAttrs.version}";
    license = lib.licenses.mit;
    mainProgram = "omp";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})

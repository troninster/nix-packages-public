{
  lib,
  stdenvNoCC,
  makeWrapper,
  bash,
  coreutils,
  ...
}:

stdenvNoCC.mkDerivation rec {
  pname = "vexora";
  version = "0.0.0";

  src = ../../tools/vexora;

  nativeBuildInputs = [
    makeWrapper
  ];

  installPhase = ''
    runHook preInstall

    install -Dm644 vexora "$out/lib/vexora/vexora"
    makeWrapper ${bash}/bin/bash "$out/bin/vexora" \
      --add-flags "$out/lib/vexora/vexora" \
      --set VEXORA_PACKAGE_VERSION "${version}" \
      --prefix PATH : ${lib.makeBinPath [ coreutils ]}

    runHook postInstall
  '';

  meta = {
    description = "VEXORA system CLI skeleton";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "vexora";
  };
}

{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  ...
}:

stdenv.mkDerivation rec {
  pname = "archon";
  version = "0.15.0";

  src = fetchurl {
    url = "https://github.com/coleam00/Archon/releases/download/v${version}/archon-linux-x64";
    hash = "sha256-797Af+VrqNOhLF+1AqFCJMF42xqEBc6tToEDDiL4lyI=";
  };

  dontUnpack = true;
  dontStrip = true;

  nativeBuildInputs = [
    autoPatchelfHook
  ];

  installPhase = ''
    runHook preInstall

    install -Dm755 "$src" "$out/bin/archon"

    runHook postInstall
  '';

  meta = {
    description = "Workflow engine CLI for AI coding agents";
    homepage = "https://github.com/coleam00/Archon";
    changelog = "https://github.com/coleam00/Archon/releases/tag/v${version}";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "archon";
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}

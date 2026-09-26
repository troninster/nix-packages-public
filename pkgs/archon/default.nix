{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  ...
}:

stdenv.mkDerivation rec {
  pname = "archon";
  version = "0.11.1";

  src = fetchurl {
    url = "https://github.com/coleam00/Archon/releases/download/v${version}/archon-linux-x64";
    hash = "sha256-ge5f6vTmyN4ZkDpsElJ0MijSNbvtuBjb7gRbId9J1Z0=";
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

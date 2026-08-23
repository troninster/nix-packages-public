{ lib
, stdenvNoCC
, python3
, makeWrapper
, ...
}:

stdenvNoCC.mkDerivation rec {
  pname = "camoufox-agent";
  version = "0.1.0";

  src = ../../tools/camoufox-agent;

  nativeBuildInputs = [
    makeWrapper
    python3
  ];

  doCheck = true;

  checkPhase = ''
    runHook preCheck
    ${python3}/bin/python -m unittest discover -s tests -v
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/camoufox-agent $out/bin
    cp camoufox_agent.py $out/lib/camoufox-agent/camoufox_agent.py

    makeWrapper ${python3}/bin/python $out/bin/camoufox-agent \
      --add-flags $out/lib/camoufox-agent/camoufox_agent.py

    runHook postInstall
  '';

  meta = {
    description = "CLI runner for local FedotFox/Camoufox browser automation";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "camoufox-agent";
  };
}

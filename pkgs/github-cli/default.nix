{
  lib,
  githubCliGoModule,
  fetchFromGitHub,
  ...
}:

githubCliGoModule rec {
  pname = "github-cli";
  version = "2.103.0";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-A3nfTjmEmbKHU4OOv510Ylc9L5dvfQQuDpboJHW/PMs=";
  };

  vendorHash = "sha256-o4p/SBVzlj+Qf3XNt5UzAbQUFrJHHOEjTCCE03+kvJs=";

  subPackages = [ "cmd/gh" ];

  ldflags = [
    "-s"
    "-w"
    "-X github.com/cli/cli/v2/internal/build.Version=${version}"
    "-X github.com/cli/cli/v2/internal/build.Date=1970-01-01"
  ];

  doCheck = false;

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    "$out/bin/gh" --version | grep -F "gh version ${version} "
    runHook postInstallCheck
  '';

  meta = {
    description = "GitHub command line tool";
    homepage = "https://github.com/cli/cli";
    changelog = "https://github.com/cli/cli/releases/tag/v${version}";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "gh";
  };
}

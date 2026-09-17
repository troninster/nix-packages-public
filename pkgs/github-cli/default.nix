{
  lib,
  buildGo127Module,
  fetchFromGitHub,
  ...
}:

buildGo127Module rec {
  pname = "github-cli";
  version = "2.101.0";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-EoKF2m5sZP+uQ5AVOKkFqSCACfkeUc7vnH8PHWCO6FE=";
  };

  vendorHash = "sha256-4KYQBgMNc/sI0mbcXSfJ7A/77VAS6NM8TOzQ3w7AlK8=";

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

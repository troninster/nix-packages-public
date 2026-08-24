{
  lib,
  buildGo125Module,
  fetchFromGitHub,
  ...
}:

buildGo125Module rec {
  pname = "github-cli";
  version = "2.98.0";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-2MktrI8FEvGkU2/cC6vrPtujl8fszuxz+Ey30WjRjhg=";
  };

  vendorHash = "sha256-fhFsu/LjLNFwexSfUsd4X74UD+AQojLcdxU5IqOi3GY=";

  subPackages = [ "cmd/gh" ];

  ldflags = [
    "-s"
    "-w"
    "-X github.com/cli/cli/v2/internal/build.Version=${version}"
    "-X github.com/cli/cli/v2/internal/build.Date=1970-01-01"
  ];

  doCheck = false;

  meta = {
    description = "GitHub command line tool";
    homepage = "https://github.com/cli/cli";
    changelog = "https://github.com/cli/cli/releases/tag/v${version}";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "gh";
  };
}

{
  lib,
  buildGo126Module,
  fetchFromGitHub,
  ...
}:

buildGo126Module rec {
  pname = "github-cli";
  version = "2.100.0";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-9tnSQPSqllE+Ke6LKyNbnOF1drzdEwesEuPdmWD1X5c=";
  };

  vendorHash = "sha256-ZqUs2BnasF3QBX0I2Sxh2A/CnO61Vy6gRn1hkf0n9AY=";

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

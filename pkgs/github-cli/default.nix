{
  lib,
  buildGo126Module,
  fetchFromGitHub,
  ...
}:

buildGo126Module rec {
  pname = "github-cli";
  version = "2.99.0";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-+66P7F+UBhqV+B/ak1LqzK8X5z+z9PLN2XhIB9FJyPg=";
  };

  vendorHash = "sha256-bVc4dhDapAp1YtO06C/nSrdxllpEhVFC2iZNPmjsJkI=";

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

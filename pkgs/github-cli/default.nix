{
  lib,
  buildGo125Module,
  fetchFromGitHub,
  ...
}:

buildGo125Module rec {
  pname = "github-cli";
  version = "2.86.0";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-+MPhDgXIVfYGp5ALI5GjRoeLRRUtNgpzUawxoqR76iE=";
  };

  vendorHash = "sha256-pBHEqMgEoR3sWNbQjGBNso7WLP9Rz2gu89Bzu+7jz5c=";

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

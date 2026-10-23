{
  lib,
  githubCliGoModule,
  fetchFromGitHub,
  ...
}:

githubCliGoModule rec {
  pname = "github-cli";
  version = "2.103.2";

  src = fetchFromGitHub {
    owner = "cli";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-GI6+/umymZvRJ/+p1ydhjM4PjrdhGnq6qsD6TeSLnE0=";
  };

  vendorHash = "sha256-e9E2iTwF8kYNgW7Vs5upQBD2VT2lJ4dZcUDUk+igq/Y=";

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

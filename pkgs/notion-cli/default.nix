{
  lib,
  buildGo125Module,
  fetchFromGitHub,
  ...
}:

buildGo125Module rec {
  pname = "notion-cli";
  version = "0.7.0";

  src = fetchFromGitHub {
    owner = "4ier";
    repo = "notion-cli";
    rev = "v${version}";
    hash = "sha256-Wy3Xi40dsmk0igxsGiX7fqvgMVnuIcdNkOefUBAgy/I=";
  };

  vendorHash = "sha256-l+js7rA49aDVu6sHcuNDSv8R8E/Fi1J7yE17uaKHhjQ=";

  subPackages = [ "." ];

  env.CGO_ENABLED = 0;

  ldflags = [
    "-s"
    "-w"
    "-X github.com/4ier/notion-cli/cmd.Version=${version}"
  ];

  postInstall = ''
    mv "$out/bin/notion-cli" "$out/bin/notion"
  '';

  doCheck = false;

  meta = {
    description = "Full-featured command line tool for Notion";
    homepage = "https://github.com/4ier/notion-cli";
    changelog = "https://github.com/4ier/notion-cli/releases/tag/v${version}";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "notion";
  };
}

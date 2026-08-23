{
  lib,
  buildGo125Module,
  fetchFromGitHub,
  ...
}:

buildGo125Module rec {
  pname = "render-cli";
  version = "2.17.0";

  src = fetchFromGitHub {
    owner = "render-oss";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-YfGgFtGq9nnucsderlNv8No8yzDM7WQ5YGqa6YEmxtc=";
  };

  vendorHash = "sha256-Ja0BcZXF8f3L+rdmk3+pyCY7khAjK+w6pyPzZdYuufs=";

  subPackages = [ "." ];

  ldflags = [
    "-s"
    "-w"
    "-X github.com/render-oss/cli/pkg/cfg.Version=${version}"
  ];

  postInstall = ''
    mv "$out/bin/cli" "$out/bin/render"
  '';

  doCheck = false;

  meta = {
    description = "Render command line tool";
    homepage = "https://github.com/render-oss/cli";
    changelog = "https://github.com/render-oss/cli/releases/tag/v${version}";
    license = lib.licenses.asl20;
    platforms = lib.platforms.linux;
    mainProgram = "render";
  };
}

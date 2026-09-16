{
  lib,
  buildGo126Module,
  fetchFromGitHub,
  ...
}:

buildGo126Module rec {
  pname = "supabase-cli";
  version = "2.117.0";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-rO02dr43J44XVic9w8yAOfbk+Pbsft5NmoV4/H+ESMI=";
  };

  sourceRoot = "source/apps/cli-go";

  vendorHash = "sha256-FHnldsmzOwO3Lz+2xrY/IUN9J+KG2zPDP+Rbwty9eYI=";

  subPackages = [ "." ];

  env.CGO_ENABLED = 0;

  ldflags = [
    "-s"
    "-w"
    "-X github.com/supabase/cli/internal/utils.Version=${version}"
  ];

  postInstall = ''
    mv "$out/bin/cli" "$out/bin/supabase"
  '';

  doCheck = false;

  meta = {
    description = "Supabase command line tool";
    homepage = "https://github.com/supabase/cli";
    changelog = "https://github.com/supabase/cli/releases/tag/v${version}";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "supabase";
  };
}

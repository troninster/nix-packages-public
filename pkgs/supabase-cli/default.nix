{
  lib,
  buildGo126Module,
  fetchFromGitHub,
  ...
}:

buildGo126Module rec {
  pname = "supabase-cli";
  version = "2.116.0";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-4TKhncuggWQhWevJEKYSRaRZ9q3eTng+3G7j0Z0Jt2w=";
  };

  sourceRoot = "source/apps/cli-go";

  vendorHash = "sha256-zW3rWDDj+7NTLBFOUDrzivxBQrjFKq374KudeGGDCpo=";

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

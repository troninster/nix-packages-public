{
  lib,
  supabaseCliGoModule,
  fetchFromGitHub,
  ...
}:

supabaseCliGoModule rec {
  pname = "supabase-cli";
  version = "2.119.0";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-0/s14MSASXqkf50kcnS7jjbSlkRicf0iV2gnSqQhInI=";
  };

  sourceRoot = "source/apps/cli-go";

  vendorHash = "sha256-CWxDovlNGhIMkfoO2wYmyCXn1sjqCy+GWzeUn9bGWxE=";

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

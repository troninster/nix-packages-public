{
  lib,
  supabaseCliGoModule,
  fetchFromGitHub,
  ...
}:

supabaseCliGoModule rec {
  pname = "supabase-cli";
  version = "2.120.0";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-WTFPNlaQ7qqTROIQ0xiobLBW/eIkPoPF9KRWpyvH5pc=";
  };

  sourceRoot = "source/apps/cli-go";

  vendorHash = "sha256-XfJZa6ksHFz1xndFqIJQ4y5tTxXWtkwOTl545BBaGUE=";

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

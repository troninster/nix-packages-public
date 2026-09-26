{
  lib,
  supabaseCliGoModule,
  fetchFromGitHub,
  ...
}:

supabaseCliGoModule rec {
  pname = "supabase-cli";
  version = "2.118.0";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-Ir516ad8+L33VkmvrANJZsImrSTNHBhm2J7+tOY96UQ=";
  };

  sourceRoot = "source/apps/cli-go";

  vendorHash = "sha256-kkUfOaZTvrU8Mly6LrKM/8jd04deucNAWw+oPE1OD5E=";

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

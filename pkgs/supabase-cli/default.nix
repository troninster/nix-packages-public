{
  lib,
  supabaseCliGoModule,
  fetchFromGitHub,
  ...
}:

supabaseCliGoModule rec {
  pname = "supabase-cli";
  version = "2.121.0";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-Vzr/kXMhIJ9G+/+htklHfqjbQWrjzRtw5kjxxY4hfQc=";
  };

  sourceRoot = "source/apps/cli-go";

  vendorHash = "sha256-eqWnUvTILOTj8ZPoL6oxMfnXu7MsP19T4A/7R1fs0oo=";

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

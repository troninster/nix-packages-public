{
  lib,
  buildGo125Module,
  fetchFromGitHub,
  ...
}:

buildGo125Module rec {
  pname = "supabase-cli";
  version = "2.98.2";

  src = fetchFromGitHub {
    owner = "supabase";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-ZiptplUqebmId7noXuVXu9G5y1SW8+FGV6WqPH8R3Cw=";
  };

  vendorHash = "sha256-2BIP500MgABRzsG13UaUVv8KKtA0dPM0U10Uk/rfVQY=";

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

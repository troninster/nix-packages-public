{
  description = "Taras Nix package collection";

  nixConfig = {
    extra-substituters = [
      "https://troninster-nix-packages.cachix.org"
    ];
    extra-trusted-public-keys = [
      "troninster-nix-packages.cachix.org-1:kUEt018f48FJjz0tD+1zId3VBBPePDusE0Lo2utVe7w="
    ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    codex = {
      url = "github:openai/codex/rust-v0.150.1";
      # Route codex's transitive rust-overlay input through our own (declared below) so a
      # single `nix flake update rust-overlay` refreshes both. Otherwise codex stays pinned
      # to whatever rust-overlay rev its upstream flake.lock happened to record, and the
      # `Update Upstreams` weekly cron can never pull in newer rustc versions that the
      # upstream codex tags require.
      inputs.rust-overlay.follows = "rust-overlay";
    };
    hermes-agent.url = "github:NousResearch/hermes-agent/main";

    # `rust-overlay` is consumed transitively via `codex.inputs.rust-overlay.overlays.default`
    # (see the `pkgsFor`/`overlays.default` blocks below). It must be declared as a direct
    # input here so `nix flake update rust-overlay` can refresh its revision; otherwise the
    # Update Upstreams weekly cron keeps pulling in new codex tags whose `rust-toolchain.toml`
    # requires rustc versions that the stale rust-overlay in flake.lock does not yet know about.
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, codex, hermes-agent, rust-overlay }:
  let
    lib = nixpkgs.lib;
    supportedSystems = [ "x86_64-linux" ];
    forAllSystems = lib.genAttrs supportedSystems;
    localPackageNames = builtins.attrNames (
      lib.filterAttrs
        (name: type: type == "directory" && builtins.pathExists (./pkgs + "/${name}/default.nix"))
        (builtins.readDir ./pkgs)
    );
    localPackagesFor = pkgs: extraArgs:
      lib.genAttrs localPackageNames (
        name: pkgs.callPackage (./pkgs + "/${name}") extraArgs
      );
    rustToolchainFor = rustBin:
      rustBin.stable."1.91.0".minimal.override {
        targets = [ "wasm32-wasip2" ];
      };
    pkgsFor = system: import nixpkgs {
      inherit system;
      config.allowUnfree = true;
      overlays = [
        codex.inputs.rust-overlay.overlays.default
        (final: prev: {
          # The pinned Codex nixpkgs input is the exact source for the Go 1.26
          # builder while the root nixpkgs input still exposes Go 1.25 only.
          buildGo126Module = codex.inputs.nixpkgs.legacyPackages.${system}.buildGo126Module;
        })
      ];
    };
    patchHermesTelegramMenuCap = pkgs: package:
      let
        baseVenv = package.passthru.hermesVenv;
        patchedVenv = baseVenv.overrideAttrs (oldAttrs: {
          postInstall = (oldAttrs.postInstall or "") + ''
            sitePackages="$out/${pkgs.python312.sitePackages}"

            copyPythonModule() {
              moduleDir="$sitePackages/$1"
              moduleSource="$(readlink -e "$moduleDir")"
              rm "$moduleDir"
              cp -r "$moduleSource" "$moduleDir"
              chmod -R u+w "$moduleDir"
            }

            # Telegram API allows 100 commands; lower menu limits hide plugin commands like /note.
            if [ -e "$sitePackages/gateway/platforms/telegram.py" ]; then
              copyPythonModule gateway
              substituteInPlace "$sitePackages/gateway/platforms/telegram.py" \
                --replace-fail "MAX_COMMANDS_PER_SCOPE = 30" "MAX_COMMANDS_PER_SCOPE = 100"
            else
              copyPythonModule hermes_cli
              substituteInPlace "$sitePackages/hermes_cli/commands.py" \
                --replace-fail "_DEFAULT_TELEGRAM_MENU_MAX_COMMANDS = 60" \
                "_DEFAULT_TELEGRAM_MENU_MAX_COMMANDS = 100"
            fi

            "$out/bin/python" -c "import hermes_cli.plugins"
          '';
        });
      in
      package.overrideAttrs (oldAttrs: {
        postInstall = (oldAttrs.postInstall or "") + ''
          for name in hermes hermes-agent hermes-acp; do
            substituteInPlace "$out/bin/$name" \
              --replace-fail "${baseVenv}" "${patchedVenv}"
          done
        '';
        passthru = (oldAttrs.passthru or { }) // {
          hermesVenv = patchedVenv;
        };
      });
    codexCargoOutputHashes = lib: {
      "crossterm-0.29.0" = "sha256-cQxQQuV+YEutuQiPurXVISq6F/99vCEk8qe5PU8BCSo=";
      "nucleo-0.5.0" = "sha256-Hm4SxtTSBrcWpXrtSqeO0TACbUxq3gizg1zD/6Yw/sI=";
      "nucleo-matcher-0.3.1" = "sha256-Hm4SxtTSBrcWpXrtSqeO0TACbUxq3gizg1zD/6Yw/sI=";
      "runfiles-0.1.0" = "sha256-uJpVLcQh8wWZA3GPv9D8Nt43EOirajfDJ7eq/FB+tek=";
      "tokio-tungstenite-0.28.0" = "sha256-V1xmnrfRWOcZZogelZEA4vvyMj2awCfHVA5/glQ6KAI=";
      "tungstenite-0.27.0" = "sha256-VVHhk7l9J/sEmG3q/UuV/sQ3f+fGsmq5vumSy8vbMvw=";
    };
    codexCargoLock = builtins.fromTOML (builtins.readFile "${codex}/codex-rs/Cargo.lock");
    codexV8Versions = map
      (package: package.version)
      (builtins.filter (package: package.name == "v8") codexCargoLock.package);
    codexV8Version =
      assert lib.assertMsg
        (builtins.length codexV8Versions == 1)
        "Codex Cargo.lock must contain exactly one v8 package";
      builtins.head codexV8Versions;
    codexReleaseProfileEnv = {
      CARGO_PROFILE_RELEASE_LTO = "false";
      CARGO_PROFILE_RELEASE_CODEGEN_UNITS = "16";
    };
    codexBuildEnv = pkgs: codexReleaseProfileEnv // {
      RUSTY_V8_ARCHIVE = pkgs.fetchurl {
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${codexV8Version}/librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz";
        hash = "sha256-o1x10fJuapg4haRbM0kKTr5U8FBQVosyuJz7QhswtYM=";
      };
      RUSTY_V8_SRC_BINDING_PATH = pkgs.fetchurl {
        url = "https://github.com/openai/codex/releases/download/rusty-v8-v${codexV8Version}/src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs";
        hash = "sha256-dyeCauR5vbZF6Acjn7EtH44uI956bPFvXuWSaQ0dhQY=";
      };
    };
    codexBuildFlags = [
      "--package"
      "codex-cli"
      "--bin"
      "codex"
      "--package"
      "codex-code-mode-host"
      "--bin"
      "codex-code-mode-host"
    ];
    codexPostInstall = inheritedHook:
      inheritedHook
      + lib.optionalString
          (inheritedHook != "" && !lib.hasSuffix "\n" inheritedHook)
          "\n"
      + ''
        test -x "$out/bin/codex"
        test -x "$out/bin/codex-code-mode-host"
      '';
    codexRecursionLimitPatch = ''
      add_recursion_limit() {
        local target="$1"
        if ! grep -Fqx '#![recursion_limit = "256"]' "$target"; then
          sed -i '1i#![recursion_limit = "256"]' "$target"
        fi
        grep -Fqx '#![recursion_limit = "256"]' "$target"
      }

      add_recursion_limit exec/src/lib.rs
      add_recursion_limit cli/src/main.rs
    '';
    codexPackageFor = pkgs: system:
      codex.packages.${system}.default.overrideAttrs (oldAttrs: {
        env = (oldAttrs.env or {}) // (codexBuildEnv pkgs);
        cargoBuildFlags = (oldAttrs.cargoBuildFlags or []) ++ codexBuildFlags;
        cargoDeps = pkgs.rustPlatform.importCargoLock {
          lockFile = "${codex}/codex-rs/Cargo.lock";
          outputHashes = codexCargoOutputHashes pkgs.lib;
        };
        # Keep the package fail-closed if a future pin drops the upstream MCP
        # attributes, and carry the recursion-limit fix into exec and cli. The
        # source root is codex-rs.
        postPatch = (oldAttrs.postPatch or "") + ''
          grep -Fqx '#![recursion_limit = "256"]' mcp-server/src/lib.rs
          grep -Fqx '#![recursion_limit = "256"]' mcp-server/src/main.rs
          ${codexRecursionLimitPatch}
        '';
        postInstall = codexPostInstall (oldAttrs.postInstall or "");
      });
  in
  {
    packages = forAllSystems (system:
      let
        pkgs = pkgsFor system;
        codexPackage = codexPackageFor pkgs system;
        hermesAgentPackage = patchHermesTelegramMenuCap pkgs hermes-agent.packages.${system}.default;
        localPackages = localPackagesFor pkgs {
          rustToolchain = rustToolchainFor pkgs.rust-bin;
        };
      in
      localPackages // {
        codex = codexPackage;
        hermes-agent = hermesAgentPackage;

        default = localPackages.camofox-browser;
      });

    checks = forAllSystems (system: lib.removeAttrs self.packages.${system} [ "default" ]);

    overlays.default = final: prev:
      let
        system = final.stdenv.hostPlatform.system;
        rustBin =
          if final ? rust-bin
          then final.rust-bin
          else (codex.inputs.rust-overlay.overlays.default final prev).rust-bin;
        rustToolchain = rustToolchainFor rustBin;
        codexPackage = codexPackageFor final system;
        hermesAgentPackage = patchHermesTelegramMenuCap final hermes-agent.packages.${system}.default;
      in
      (localPackagesFor final { inherit rustToolchain; }) // {
        codex = codexPackage;
        hermes-agent = hermesAgentPackage;
      };
  };
}

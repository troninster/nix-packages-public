# uv compiles files in parallel workers; marshal output can depend on the
# previous files handled by each worker (astral-sh/uv#10619). Recompile only
# Hermes's own bytecode with compileall's sorted traversal and one worker.
uv2nix:
uv2nix // {
  lib = uv2nix.lib // {
    workspace = uv2nix.lib.workspace // {
      loadWorkspace = args:
        let workspace = uv2nix.lib.workspace.loadWorkspace args;
        in workspace // {
          mkPyprojectOverlay = options: final: prev:
            let packages = workspace.mkPyprojectOverlay options final prev;
            in packages // {
              hermes-agent = packages.hermes-agent.overrideAttrs (old: {
                postInstall = (old.postInstall or "") + ''
                  PYTHONHASHSEED=0 ${final.python.interpreter} -m compileall \
                    --invalidation-mode checked-hash -q -f -j 1 "$out/lib"
                '';
              });
            };
        };
    };
  };
}

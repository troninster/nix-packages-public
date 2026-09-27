{ symlinkJoin, makeWrapper, unwrapped }:

symlinkJoin {
  name = "codex-${unwrapped.version}";
  inherit (unwrapped) version;
  paths = [ unwrapped ];
  nativeBuildInputs = [ makeWrapper ];
  passthru = { inherit unwrapped; };
  meta = unwrapped.meta // { mainProgram = "codex"; };

  postBuild = ''
    # The upstream daemon seeds a mutable CLI copy and its own updater. Keep
    # ordinary Nix sessions on the packaged embedded server instead. This is a
    # supported feature override, not a second CLI argument parser; exec,
    # app-server, resume/fork and explicitly remote connections keep their args.
    wrapProgram "$out/bin/codex" --add-flags "--disable daemon_auto_start"

    # --version alone never exercises the startup policy that broke 0.157.1.
    mkdir -p "$TMPDIR/codex-startup-check"
    CODEX_HOME="$TMPDIR/codex-startup-check" "$out/bin/codex" features list \
      > "$TMPDIR/codex-startup-features"
    grep -Eq '^daemon_auto_start[[:space:]]+[^[:space:]]+[[:space:]]+false$' \
      "$TMPDIR/codex-startup-features"
    test ! -e "$TMPDIR/codex-startup-check/packages/app-server-daemon/current"
    "$out/bin/codex" --version | grep -Fx 'codex-cli ${unwrapped.version}'
    test -x "$out/bin/codex-code-mode-host"
  '';
}

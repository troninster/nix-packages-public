# Patch Provenance

Axis-A `patched` components record their upstream source and pin, the exact
local change and mechanism, and the reason for the change here. Future patched
components record provenance the same way.

## Hermes Agent: Telegram compatibility and sealed-package completeness

- **Classification and date:** Axis A `patched`, recorded 2026-07-22. This is a
  runtime component, not a project.
- **Upstream and pin:** `github:NousResearch/hermes-agent/main` is declared as
  the `hermes-agent` flake input. `flake.lock` fixes the resolved Git revision
  and NAR hash.
- **Patch:** `patchHermesTelegramMenuCap` in `flake.nix` copies the relevant
  Python module into a writable derived virtual environment and changes
  `MAX_COMMANDS_PER_SCOPE` from 30 to 100. Its compatibility path changes
  `_DEFAULT_TELEGRAM_MENU_MAX_COMMANDS` from 60 to 100 when that is the
  upstream layout, then rewires the Hermes launchers to the derived environment.
  For upstream `v2026.8.13`, which imports the top-level
  `registration_lifecycle` module but omits it from the wheel metadata, the same
  derived environment installs that exact source module. Releases such as
  `v2026.8.31` that declare and package the module keep the upstream-installed
  implementation instead.
- **Mechanism:** every textual substitution uses `substituteInPlace` with
  `--replace-fail`, so an upstream source drift that invalidates the expected
  text fails the build loudly. The registration helper requires the exact
  upstream import, source module, and canonical packaging declaration. A
  declared module must already exist in the built environment; an undeclared
  module is copied only when absent. Contradictory layouts fail before the final
  import smoke test.
- **Reason:** Telegram's Bot API permits 100 commands per scope. The lower
  Hermes cap hid later plugin commands such as `/note` from the menu. The
  missing top-level module otherwise makes the packaged gateway crash before
  it can create the Telegram adapter.

## FedotFox / Camofox browser: NixOS runtime adaptations

- **Classification and date:** Axis A `patched`, recorded 2026-07-22. This is a
  runtime component, not a project.
- **Upstream and pins:** `pkgs/camofox-browser/default.nix` pins
  `jo-inc/camofox-browser` by Git revision and fixed-output hash, pins the
  `daijro/camoufox` Linux engine by release, version, and hash, and pins npm
  dependencies with `npmDepsHash`.
- **Patches:** the derivation makes `camoufox-js` accept the Nix-store engine;
  prevents the default addon from trying to mutate that read-only store; gives
  fresh empty sessions a grace period; raises the local request-timeout floor;
  keeps idle shutdown idempotent on affected upstream versions; and makes
  active health probes skip idle browsers, avoid overlap, and reset state after
  relaunch.
- **Mechanism:** the changes are declarative `substituteInPlace`
  transformations with `--replace-fail`. The engine-path, default-addon, and
  idle-shutdown changes detect supported native and legacy layouts, preserve
  the native behavior, and otherwise apply the fail-loud compatibility patch.
  Unknown layouts stop the build.
- **Reason:** these adaptations make the pinned browser run reproducibly from
  the immutable Nix store and avoid observed cold-start, cleanup, and health
  probe races on NixOS.

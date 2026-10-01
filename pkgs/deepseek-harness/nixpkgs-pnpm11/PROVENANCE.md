# Package-local pnpm 11 backport

These files are copied verbatim from NixOS/nixpkgs at
`f9679155c9fc15d4722000c392337925c0a6ab42`, the merge commit of
[PR #522703](https://github.com/NixOS/nixpkgs/pull/522703).

The collection's pinned nixpkgs only supports pnpm 10. DeepSeek Harness pins
pnpm 11.7.0, whose SQLite store requires deterministic metadata normalization
and a SQL dump/reconstruction cycle (fetcherVersion 4) for fixed-output builds.
This backport is used only by DeepSeek Harness; it does not replace shared hooks.

Source files:

- [fetch-pnpm-deps/default.nix](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/build-support/node/fetch-pnpm-deps/default.nix)
- [fetch-pnpm-deps/pnpm-config-hook.sh](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/build-support/node/fetch-pnpm-deps/pnpm-config-hook.sh)
- [fetch-pnpm-deps/serve.nix](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/build-support/node/fetch-pnpm-deps/serve.nix)
- [pnpm-fixup-state-db/package.nix](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/by-name/pn/pnpm-fixup-state-db/package.nix)
- [pnpm-fixup-state-db/src/index.ts](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/by-name/pn/pnpm-fixup-state-db/src/index.ts)
- [pnpm-fixup-state-db/src/package.json](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/by-name/pn/pnpm-fixup-state-db/src/package.json)
- [pnpm-fixup-state-db/src/package-lock.json](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/by-name/pn/pnpm-fixup-state-db/src/package-lock.json)
- [pnpm-fixup-state-db/src/tsconfig.json](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/pkgs/by-name/pn/pnpm-fixup-state-db/src/tsconfig.json)
- [COPYING](https://github.com/NixOS/nixpkgs/blob/f9679155c9fc15d4722000c392337925c0a6ab42/COPYING)

The upstream MIT copyright and permission notice is retained in COPYING.
No source modifications were made to the vendored files.

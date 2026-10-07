# Symphony release pilot contract v1

This is the first source/shadow checkpoint, not a published READY release.
Only Symphony TS is in scope. Its component root is `pkgs/symphony-ts` and
owns its Nixpkgs lock; the host must not override that input through `follows`.
The original ordinary package remains available during the measured cutover.

## Build and trust boundaries

`scripts/build-symphony-component` checks the component flake without resolving
or writing a different lock, builds, requires a byte-identical repeat build,
and runs the CLI help smoke. This proof belongs in public GitHub CI, without
requiring a Cachix upload. It is not by itself a delivery/READY proof.

The independently approved public signer is `troninster-symphony-releases-1`.
Its private key is only the `SYMPHONY_RELEASE_SIGNING_KEY` GitHub Secret in
`troninster/nix-packages-public`; the public key belongs in dotfiles trust.
The private dotfiles system-artifact signer is separate and is not reused.
Build/PR code must not receive the signing secret. The eventual trusted signing
job imports verified build outputs, never executes a candidate recipe with the
key in its environment, and signs only the selected full runtime closure.
New signing/publication workflows require normal review, not pin-only automerge.

## Immutable record

The consumer shadow reader accepts exactly these top-level fields:

| Field | Contract |
|---|---|
| `schema`, `status` | Integer `1`, literal `READY` |
| `component`, `platform` | `symphony-ts`, `x86_64-linux` |
| `source` | Fields below, binding the reviewed source/input bytes |
| `package` | `version`, `drvPath`, `storePath` of the ordinary evaluated package |
| `closure` | Full reachable runtime graph keyed by store path |
| `verification` | Policy `symphony-repeat-cold-v1` and successful `repeatBuild`, `coldImport`, `smoke` |
| `releaseId` | SHA256 of the canonical immutable identity below |

`source` contains exactly `repository` (`troninster/nix-packages-public`),
`revision` (full recipe-source Git SHA), `directory` (`pkgs/symphony-ts`),
`componentSha256`, `lockSha256` and `upstreamRevision` (the unique pinned
upstream SHA in `default.nix`). Full repository SHA is provenance, not a
requirement that common main must stop advancing during the build.

`componentSha256` is SHA256 of a canonical JSON object mapping each of
`pkgs/symphony-ts/default.nix`, `pkgs/symphony-ts/flake.nix` and
`pkgs/symphony-ts/flake.lock` to the hexadecimal SHA256 of its raw bytes.
`lockSha256` is SHA256 of the raw component lock bytes. There are currently no
external local recipe helpers; adding one requires including its effective
identity in the reviewed contract rather than silently hashing the whole repo.

Each `closure` entry contains exactly `narHash` (canonical SHA256 SRI),
`narSize` (nonnegative integer) and `references` (sorted unique full store paths).
All references must be present; every entry must be reachable from the root.
Derivations/build intermediates are not part of the runtime export.

Canonical JSON means UTF-8, sorted keys, compact separators `(',', ':')`,
`ensure_ascii=True`, no trailing newline. `releaseId` hashes the object containing
only `component`, `platform`, `source`, `package` and `closure`. The record's
verification metadata is still authenticated by its trusted publication below;
transport locations/availability are not a second version selector.

## Catalog and publication boundary

The reserved producer catalog branch is `component-releases`. Under
`releases/symphony-ts/x86_64-linux/`, immutable `<releaseId>.json` holds the
record and `latest.json` contains exactly `schema: 1`, `releaseId` and
`recordSha256` (SHA256 of the canonical full record). A reader resolves one
exact catalog commit then reads all records from that SHA. Source files are
read at the record's exact `source.revision`, never substituted with latest main.

This branch/records have **not been published by the source checkpoint**.
The next stage must establish reviewed trusted publication ownership before
enabling an active consumer. An arbitrary checksum and self-reported successful
flags do not authenticate a release. The owned catalog must bind an approved
producer and reviewed source to the exact bytes, independently of an expiring
CI run. Nix signatures separately authenticate each runtime store path.

Publish READY only after remote repeat proof, immutable signed GitHub Release
delivery, a real cold import with original trusted signatures and application
builders disabled, exact NAR/reference comparison, and CLI smoke. Conflicting
NAR hashes for the same store path fail closed; never borrow trust or overwrite
existing runtime bytes. Cachix becomes optional only after this full path works.

## Consumer handover

Dotfiles initially observes metadata only, disabled by default. Its normal
`flake.lock` remains the sole version selector; no pin or installed package
changes merely because a record parses. Next, match the actual evaluated
component derivation/output to READY and validate all other component inputs
and outputs unchanged. Recreate only the selected pin against a fresh dotfiles
base after unrelated advancement, without repeating an unchanged package build.

When enabling the new Symphony writer, disable both old remote and local
Symphony promotion routes in the same cutover. Other packages and the current
human-gated installer remain unchanged. Installation requires its own exact
candidate confirmation and fresh sudo; remote build success is not activation.

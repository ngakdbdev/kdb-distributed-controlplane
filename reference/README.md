# Reference material - not a dependency

`kx-official-kdb-tick/` is a **git submodule** pointing at KX's own public repo
(`KxSystems/kdb-tick`) - the classic `tick.q` / `u.q` / `r.q` scripts KX has published for years.

**Why it's here**: for the team to read and compare against while iterating on
`data-plane/q/*.q` next week - it's useful to see KX's own baseline pattern side by side with
our sharded, self-healing version.

**Why it's a submodule and not copied in**: a submodule only stores a pointer (a commit hash) in
our repo - the actual files live in and are fetched directly from KX's own GitHub repo when you
run `git submodule update --init`. Nothing from it is duplicated into our git history.

**What it is NOT**:
- **Not the kdb+/KDB-X engine.** The actual `q` binary and license are proprietary, require a KX
  account, and are deliberately excluded from this repo (see `.gitignore` and
  `data-plane/docker/kdbx/PUT_KDBX_BINARY_HERE.txt`). Nothing here changes that - you still need
  to download KDB-X Community Edition yourself.
- **Not a build dependency.** Nothing in `docker-compose.yml`, the Dockerfiles, or the Helm chart
  references this folder. It's here to read, not to run against.
- **Not license-cleared for redistribution as-is.** KX's own README for that repo doesn't attach a
  machine-readable license and explicitly recommends against applications hot-linking to it. We're
  respecting that by keeping it as a submodule pointer only, never copying its content into our
  own source files.

If you clone this repo and don't need the reference material, skip it - `git clone` alone won't
pull submodule contents; that only happens if you explicitly run `git submodule update --init`.

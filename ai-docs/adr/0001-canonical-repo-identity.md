# Canonical repo identity is `owner/repo` from `git remote`, on both sides

## Context

`session.repoShort` was derived from path segments (last 1–2 segments of `cwd`), which produced the right-looking `<org>/<repo>` only when the user happened to keep projects under `~/code/<org>/<repo>/` and launched Claude from the repo root. Open Claude from any subdirectory and the field collapsed to `<parent-subdir>/<subdir>` (e.g. `a/b`), silently misidentifying the repo in groupings, displays, and downstream consumers. Worse, the session side had `repo` = bare name and `repoShort` = path-junk, while the PR side and the schema docstrings both treated `repo` = `owner/repo` and `repoShort` = bare name — the two sides of the report disagreed about what the same field name meant.

## Decision

Sessions and PRs share one canonical shape, derived from `git remote get-url origin` and *not* from path layout:

- `repo` = full `owner/repo` (e.g. `myorg/a`)
- `repoShort` = bare repo name (e.g. `a`)

Fallback when no usable remote is reachable (no `.git` above `cwd`, or `.git` found but `remote.origin.url` is missing/unreadable): both fields become `local:<last-cwd-segment>` (e.g. `local:scratch`). The `local:` prefix is on **both** fields so local-only work groups together in `minutesByRepo` and can never collide with a real GitHub repo of the same leaf name.

Correlation joins on `session.repo == pr.repo` (case-insensitive) — full `owner/repo`, not bare name. All `totals.*ByRepo` aggregations key on `owner/repo` for the same reason. The bare-name field stays available for display.

Helper functions in `lib/utils.py` are renamed to match their new return shape: `repo_full(cwd)` returns `owner/repo` (or `local:…`), `repo_short(cwd)` returns the bare name (or `local:…`).

This is a schema-breaking change: bump `report.schema.json` `$id` to `v3.0.0`, add a CHANGELOG entry, and require fresh `generate.py` runs (no `--rerender` backfill, mirroring how v2.0.0 handled `activeDurationMin`). The companion visualizer repo must migrate in lockstep.

## Considered and rejected

- **Add a new `repoFull` field, leave the existing fields alone.** Strictly additive schema bump. Rejected because the broken `repoShort` value would live in the report forever.
- **Keep field names, put `owner/repo` into `session.repoShort` only.** Literal interpretation of the original bug report. Rejected because it would make `repoShort` mean different things on sessions (`myorg/a`) vs PRs (`a`) — a permanent footgun.
- **Anchor path-based shortening on `git_root` (e.g. `git_root.parent.name + '/' + git_root.name`).** Cwd-depth-independent but still wrong for anyone who doesn't store projects under `<org>/<repo>` directories.
- **Best-effort backfill from `cwd` on `--rerender`.** Cheap, but cwd may no longer exist on disk by rerender time — backfill would then fall through to `local:…` even when the original session ran against a real repo. Silently wrong is worse than loudly absent.
- **Join correlation on bare name (today's behavior).** Tolerates the fork-of-upstream case where local origin and PR origin differ in owner. Rejected because it permits cross-org false matches for any two same-named repos, and the fork case isn't observed in practice.

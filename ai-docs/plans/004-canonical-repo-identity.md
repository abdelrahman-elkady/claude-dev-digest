status: implemented
---

# 004 — Canonical repo identity from `git remote` on both sides

Background: [ADR 0001](../adr/0001-canonical-repo-identity.md). Read it first for the *why* and the rejected alternatives.

## Outcome

Session-side `repo` / `repoShort` fields stop being derived from path segments and instead mirror the PR side and the schema docstrings:

| Field | Value (with remote) | Fallback (no `.git` above cwd, or `.git` but no `remote.origin.url`) |
|---|---|---|
| `repo` | `owner/repo` (e.g. `myorg/a`) | `local:<last-cwd-segment>` |
| `repoShort` | bare repo name (e.g. `a`) | `local:<last-cwd-segment>` |

Correlation joins on `session.repo == pr.repo` (full owner/repo, case-insensitive). All `totals.*ByRepo` aggregations key on full `owner/repo`.

## Files to change

- **`plugin/skills/claude-dev-digest/lib/utils.py`**
  - Rename `repo_name` → `repo_full`. Return value becomes `owner/repo` (parse both groups from the `remote.origin.url` regex, not just `m.group(2)`). On no-remote / no-git path, return `local:<last-cwd-segment>`.
  - Update `repo_short` to return the bare repo name (parse `m.group(2)`), reusing `git_root_for_cwd`. Same `local:<last-cwd-segment>` fallback.
  - Both helpers must use the **same** fallback string for a given cwd — derive it once.
  - Keep `_GIT_ROOT_CACHE` and per-helper caches.
  - `git_root_for_cwd` and `repo_relative_path` are unchanged.

- **`plugin/skills/claude-dev-digest/lib/scanner.py`**
  - Update imports (`repo_full` instead of `repo_name`).
  - `"repo": repo_full(cwd)`, `"repoShort": repo_short(cwd)` — field names unchanged.

- **`plugin/skills/claude-dev-digest/lib/correlate.py`**
  - Change join key from `pr["repoShort"]` to `pr["repo"]` (and the corresponding session side from `session["repo"]` — already correct after the scanner change). Keep `.lower()`.

- **`plugin/skills/claude-dev-digest/lib/report.py`**
  - Replace every `repoShort` used as a grouping key with `repo`. Locations (search hits): `by_repo[session["repoShort"]…]`, `pr_by_repo[p["repoShort"]]`, `_sum_minutes(group_key="repoShort", …)` (3 calls), `by_repo.setdefault(p["repoShort"], …)`, the markdown header `f"### [{cat}] {session['repoShort']} …"`.
  - Display strings (markdown header) can stay on `repoShort` if that reads better — pick one and be consistent. The grouping keys must move; the display strings are a judgment call.

- **`plugin/skills/claude-dev-digest/lib/github.py`** — no change. `pr.repo` and `pr.repoShort` are already correct.

- **`report.schema.json`**
  - Bump `$id` to `claude-dev-digest/report/v3.0.0`.
  - Session `repo` description: "Full `owner/repo` from `git remote`, or `local:<segment>` when no remote is reachable."
  - Session `repoShort` description: "Bare repo name (no owner), or `local:<segment>` when no remote is reachable."
  - PR descriptions already match — leave them.

- **`CHANGELOG.md`**
  - New `## 3.0.0` section above `## 2.0.0`. Mark **Changed (breaking)** for `session.repo` (now `owner/repo`, was bare name) and `session.repoShort` (now bare name, was path-suffix). Note correlation join key change and `totals.*ByRepo` re-keying. Note no `--rerender` backfill — fresh `generate.py` required (same posture as v2.0.0's `activeDurationMin`).

- **`CONTEXT.md`**
  - Section "1. Repo identity comes from `git remote`, not the directory name" — rewrite to reflect the new helpers (`repo_full`, `repo_short`), the `local:` fallback, and that **both** sides of the report use the same shape now.
  - Update "join key is **`session.repo == pr.repoShort`**" → **`session.repo == pr.repo`** wherever it appears (intro + section 1).
  - Update the data-flow diagram label `lib/utils.repo_name` → `lib/utils.repo_full`.
  - Update the module map line for `utils.py`.

- **`CLAUDE.md`**
  - "Repo identity comes from `git remote`, not directory names. Always call `lib.utils.repo_name(cwd)`" → swap to `repo_full(cwd)`.

- **Visualizer (separate repo `weekly-report-visualizer`)** — out of scope for this PR but call it out in the PR description: any code reading `session.repoShort` as `owner/repo` must switch to `session.repo`. Grouping keys in `totals.*ByRepo` are now `owner/repo`.

## `--rerender` behavior

No backfill. If an input `report.json` has a schema version below v3, fail loudly with a message pointing at `generate.py` (no `--rerender`). Mirror the existing v1→v2 message style.

## Smoke checks

```bash
# Full run, then assert new shape
python3 plugin/skills/claude-dev-digest/generate.py --output-dir /tmp/pr-test --format json
python3 -c "
import json, re
d = json.load(open('/tmp/pr-test/report.json'))
assert d['totals']['sessions'] > 0
for s in d['sessions']:
    assert 'repo' in s and 'repoShort' in s
    assert s['repo'].startswith('local:') or '/' in s['repo'], s['repo']
    assert s['repoShort'].startswith('local:') or '/' not in s['repoShort'], s['repoShort']
# All totals.*ByRepo keys are owner/repo (or local:…)
for key in ('minutesByRepo', 'activeMinutesByRepo', 'idleMinutesByRepo', 'sessionsByRepo'):
    for k in d['totals'].get(key, {}):
        assert k.startswith('local:') or '/' in k, (key, k)
print('OK', d['totals']['sessions'], 'sessions')
"

# Schema still validates
python3 -c "from jsonschema import validate; import json; validate(json.load(open('/tmp/pr-test/report.json')), json.load(open('report.schema.json')))"
```

Manual cases to spot-check in the report:
1. Session opened from the repo root → `repo == 'myorg/a'`, `repoShort == 'a'`.
2. Session opened from a subdir like `a/b/c` → same values as case 1.
3. Session in a directory with no git anywhere up the tree → both fields `local:<seg>`.
4. Session in a repo with `.git` but no `remote.origin.url` → both fields `local:<git_root.name>`.
5. Correlation: a session previously mis-correlating against a same-leaf-named repo in another org no longer matches; a session opened from a subdir now correlates with PRs in the parent repo (was likely failing to before).

## Gotchas

- `_REPO_CACHE` and `_GIT_ROOT_CACHE` are module-level; if you add a new helper it must share the same git-root cache to avoid double-walking.
- The `local:` prefix carries a colon — make sure no downstream code naively splits on `/` and expects two parts.
- Cwd may no longer exist on disk by report time (project moved/deleted). `git_root_for_cwd` already handles this by walking up and returning `None`; just make sure the new helpers route that to the `local:` fallback, not to a crash.
- The `remote.origin.url` regex in `repo_full` must capture **both** groups now. Existing regex: `r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$"` — group 1 is owner, group 2 is repo. Compose as `f"{m.group(1)}/{m.group(2)}"`.

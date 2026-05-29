"""Pure helpers shared across modules: time parsing, text shortening, repo resolution."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Strip <ide_opened_file>...</ide_opened_file>, <ide_selection>..., <command-name>...,
# etc. — IDE/CLI launchers prepend these wrappers to user prompts and they hide the
# real text behind a wall of XML-ish noise. We don't try to parse them, just remove
# them and any trailing whitespace.
_XML_WRAPPER_RE = re.compile(r"<([a-zA-Z][\w-]*)\b[^>]*>.*?</\1>", re.DOTALL)
_BARE_TAG_RE = re.compile(r"<[^>]+>")

# Parses `owner/repo` out of a git remote URL (ssh or https, with/without .git).
# Group 1 is the owner, group 2 is the repo.
_REMOTE_URL_RE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


def parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a tz-aware datetime, or None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def shorten(text: str | None, n: int = 140) -> str:
    """Strip IDE/CLI XML wrappers, collapse whitespace, clip to n chars (with ellipsis)."""
    if not text:
        return ""
    text = _XML_WRAPPER_RE.sub("", text)
    text = _BARE_TAG_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def text_from_content(content) -> str:
    """Extract plain text from a Claude message content field (str | list[block])."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
        return "\n".join(out)
    return ""


# Per-cwd caches so repeated lookups never re-walk the tree or re-shell-out.
# `_GIT_ROOT_CACHE` stores the enclosing .git root (None when there is none);
# `_REMOTE_CACHE` stores the parsed remote (None when unreachable), shared by
# repo_full and repo_short so the `git config` subprocess runs at most once per
# cwd. Absence of a key means "not yet looked".
_GIT_ROOT_CACHE: dict[str, Path | None] = {}
_REMOTE_CACHE: dict[str, tuple[str, str] | None] = {}


def git_root_for_cwd(cwd: str) -> Path | None:
    """Return the .git-containing root above `cwd`, or None. Cached per cwd."""
    if not cwd:
        return None
    if cwd in _GIT_ROOT_CACHE:
        return _GIT_ROOT_CACHE[cwd]
    root: Path | None = None
    p = Path(cwd)
    while p != p.parent:
        if (p / ".git").exists():
            root = p
            break
        p = p.parent
    _GIT_ROOT_CACHE[cwd] = root
    return root


def _remote_owner_repo(cwd: str) -> tuple[str, str] | None:
    """Parse (owner, repo) from the enclosing git root's remote.origin.url.

    Uses `git_root_for_cwd` to find the root, then runs
    `git -C <root> config --get remote.origin.url` and matches it against
    `_REMOTE_URL_RE`. Returns None when there is no git root, the git call
    fails, or the URL doesn't match. Cached per cwd so the subprocess runs at
    most once per cwd (both `repo_full` and `repo_short` resolve through here).
    """
    if cwd in _REMOTE_CACHE:
        return _REMOTE_CACHE[cwd]
    result: tuple[str, str] | None = None
    git_root = git_root_for_cwd(cwd)
    if git_root is not None:
        try:
            url = subprocess.check_output(
                ["git", "-C", str(git_root), "config", "--get", "remote.origin.url"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            m = _REMOTE_URL_RE.search(url)
            if m:
                result = (m.group(1), m.group(2))
        except (subprocess.CalledProcessError, FileNotFoundError):
            result = None
    _REMOTE_CACHE[cwd] = result
    return result


def _local_fallback(cwd: str) -> str:
    """Return a `local:` label for a cwd with no reachable remote.

    If a git root exists above `cwd`, use its directory name (depth-independent).
    Otherwise fall back to the last non-empty path segment of `cwd`, or
    `local:unknown` when `cwd` has no segments. Both `repo_full` and `repo_short`
    call this so they agree on the label for a given cwd.
    """
    git_root = git_root_for_cwd(cwd)
    if git_root is not None:
        return "local:" + git_root.name
    parts = [p for p in cwd.strip("/").split("/") if p]
    if parts:
        return "local:" + parts[-1]
    return "local:unknown"


def repo_full(cwd: str) -> str:
    """Resolve a cwd to its full `owner/repo` GitHub identity.

    Reads `remote.origin.url` from the enclosing git root and returns
    `owner/repo`. Falls back to a `local:` label when no remote is reachable.
    The expensive parse is cached in `_remote_owner_repo`.
    """
    if not cwd:
        return ""
    owner_repo = _remote_owner_repo(cwd)
    if owner_repo:
        return f"{owner_repo[0]}/{owner_repo[1]}"
    return _local_fallback(cwd)


def repo_short(cwd: str) -> str:
    """Resolve a cwd to its bare repo name (e.g. 'node-app').

    Reads `remote.origin.url` from the enclosing git root and returns the repo
    name. Falls back to a `local:` label when no remote is reachable. The
    expensive parse is cached in `_remote_owner_repo`.
    """
    if not cwd:
        return ""
    owner_repo = _remote_owner_repo(cwd)
    if owner_repo:
        return owner_repo[1]
    return _local_fallback(cwd)


def repo_relative_path(full_path: str, cwd: str) -> str:
    """Convert an absolute file path to repo-relative, using `cwd` to find the root."""
    if not full_path or not cwd:
        return full_path
    git_root = git_root_for_cwd(cwd)
    if not git_root:
        return full_path
    try:
        return str(Path(full_path).resolve().relative_to(git_root.resolve()))
    except (ValueError, OSError):
        return full_path


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

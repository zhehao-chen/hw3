"""search_repo: literal, case-insensitive text search over the private codebase.

Its definition is search_repo.json. It reads a prebuilt index,
fixtures/search_index/index.json, of every text file under fixtures/repo except
config/, and the index records when it was built. It can read the files
themselves instead (source "live_files"). Results come back as JSON, in path order:
the query and the filter echoed, query_valid, the count of every matching line, the
lines shown (capped by response_format), whether they were truncated, and the
index's age.

**Malformed**, for Assignment 3 (Part A, task 1), is any of three things:
  1. a query that is empty or only whitespace;
  2. a query longer than MAX_QUERY_CHARS;
  3. a filter that is not a valid glob (glob_problem, below).
A malformed search is a ToolError, an is_error result; a search that matches nothing
is a success with count 0 and query_valid true. The two differ in the raw response.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = {"config", "node_modules", ".git"}
CAPS = {"concise": (10, 120), "detailed": (20, 300)}   # lines shown, characters per line
MAX_QUERY_CHARS = 200
INDEX = "fixtures/search_index/index.json"
GLOB_CHARS = re.compile(r"^[A-Za-z0-9_.\-/*?\[\]{},]+$")


def glob_problem(pattern: str) -> str | None:
    """Why `pattern` is not a valid path glob, or None when it is one."""
    if pattern.startswith("/"):
        return "a glob is relative to the repository root; it cannot start with /"
    if ".." in pattern.split("/"):
        return "a glob cannot leave the repository (..)"
    if not GLOB_CHARS.match(pattern):
        return "a glob uses letters, digits, / . _ - and the wildcards * ? [ ] { }; no spaces, colons or quotes"
    depth = 0
    for ch in pattern:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth not in (0, 1):
            return "a glob's [ and ] must pair up"
    if depth:
        return "a glob's [ and ] must pair up"
    if pattern.count("{") != pattern.count("}"):
        return "a glob's { and } must pair up"
    return None


def problem(query, filter=None) -> str | None:
    """Why this search is malformed, or None when it is well-formed."""
    text = str(query or "")
    if not text.strip():
        return "the query is empty"
    if len(text) > MAX_QUERY_CHARS:
        return f"the query is {len(text)} characters long; the cap is {MAX_QUERY_CHARS}"
    if filter not in (None, ""):
        return glob_problem(str(filter))
    return None


def _expand(pattern: str) -> list[str]:
    """{a,b} alternatives, which fnmatch does not know."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    return [p for option in match.group(1).split(",")
            for p in _expand(pattern[:match.start()] + option + pattern[match.end():])]


def _matches(path: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return any(fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path, p.replace("**/", ""))
               for p in _expand(pattern))


def load_index(root: Path) -> dict:
    return json.loads((Path(root) / INDEX).read_text())


def _disk(repo_root: Path) -> dict[str, list[str]]:
    files = {}
    for path in sorted(Path(repo_root).rglob("*")):
        rel = path.relative_to(repo_root)
        if path.is_file() and not (set(rel.parts) & EXCLUDED_DIRS):
            try:
                files[rel.as_posix()] = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
    return files


def age(built_at: str, now: float | None = None) -> str:
    built = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    days = int(((now or time.time()) - built.timestamp()) // 86400)
    return "today" if days < 1 else f"{days} day{'s' if days != 1 else ''} ago"


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, checked at the boundary: one line
    per field at fault, saying what is wrong and what would be right."""
    out = []
    query = args.get("query")
    if isinstance(query, str):
        if not query.strip():
            out.append("query: empty or only whitespace; give 1 to "
                       f"{MAX_QUERY_CHARS} characters of literal text, such as an identifier")
        elif len(query) > MAX_QUERY_CHARS:
            out.append(f"query: {len(query):,} characters; the cap is {MAX_QUERY_CHARS}. "
                       "Search for one distinctive term, not a passage")
    glob = args.get("path_glob")
    if isinstance(glob, str):
        why = "the glob is empty" if not glob.strip() else glob_problem(glob)
        if why:
            out.append(f"path_glob: {glob!r} is not a valid glob ({why}); "
                       "use one such as \"src/**/*.ts\", or null to search every file")
    return out


def run(ctx, query, path_glob=None, source="index", response_format="concise"):
    from tools import ToolError

    problems = check({"query": query, "path_glob": path_glob})
    if problems:
        raise ToolError("malformed search, nothing searched: " + "; ".join(problems))
    shown_cap, line_cap = CAPS[response_format]
    repo_root = Path(ctx.repo_root)
    if source == "live_files":
        files, index_age = _disk(repo_root), None
    else:
        index = load_index(repo_root.parent.parent)
        files, index_age = index["files"], age(index["built_at"])
    needle = str(query).strip().lower()
    hits = [{"path": rel, "line": number, "text": line.strip()[:line_cap]}
            for rel, lines in sorted(files.items()) if _matches(rel, path_glob)
            for number, line in enumerate(lines, 1) if needle in line.lower()]
    return json.dumps({"query": query, "path_glob": path_glob, "query_valid": True,
                       "count": len(hits), "shown": min(len(hits), shown_cap),
                       "truncated": len(hits) > shown_cap, "source": source,
                       "index_age": index_age, "results": hits[:shown_cap]})

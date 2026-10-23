"""read_file: return one file of the private codebase, capped by response_format.

Its definition is read_file.json. It reads the same files search_repo searches:
everything under fixtures/repo except config/. A path is relative to
fixtures/repo, as search results print it. The result is JSON, in the shape
read_file.json's output_schema declares; a file longer than the cap comes back cut,
with "truncated": true.

A path must match a file exactly, case included. The file system is not asked
whether it exists: on a case-insensitive disk, which is macOS's default, opening
src/report.ts opens src/Report.ts and reports success. The path is compared with
the repository's own file list instead, and a miss names the nearest paths
(Assignment 3, Part B, task 3).
"""

import difflib
import json
from pathlib import Path

CAPS = {"concise": 6_000, "detailed": 20_000}   # characters returned
MAX_NEAR = 5          # near misses named in an error
MAX_LISTED = 8        # files named when the path is a directory


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, checked at the boundary."""
    path = args.get("path")
    if isinstance(path, str) and not path.strip():
        return ["path: empty; give a file's path relative to the repository root, "
                "exactly as code_search printed it, such as src/util/retry.ts"]
    return []


def repo_files(root: Path) -> list[str]:
    """Every readable file, as a path relative to the repository root, in its real case."""
    from tools.search_repo import EXCLUDED_DIRS

    out = []
    for path in Path(root).rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not (set(rel.parts) & EXCLUDED_DIRS):
            out.append(rel.as_posix())
    return sorted(out)


def near_misses(rel: str, files: list[str], n: int = MAX_NEAR) -> list[str]:
    """The existing paths closest to `rel`: the same path in another case first, then
    the same name with another extension, then the same file name in another
    directory, then paths that differ by a character or two."""
    want = rel.lower()
    stem, name = want.rsplit(".", 1)[0], want.rsplit("/", 1)[-1]
    ranked = []
    for f in files:
        low = f.lower()
        ratio = difflib.SequenceMatcher(None, want, low).ratio()
        if low == want:
            rank = 0
        elif low.rsplit(".", 1)[0] == stem:
            rank = 1
        elif low.rsplit("/", 1)[-1] == name:
            rank = 2
        elif ratio >= 0.85:
            rank = 3
        else:
            continue
        ranked.append((rank, -ratio, f))
    return [f for _, _, f in sorted(ranked)[:n]]


def run(ctx, path, response_format="concise"):
    from harness.formats import normalize_path
    from tools import ToolError
    from tools.search_repo import EXCLUDED_DIRS

    rel = normalize_path(str(path))
    if rel in (None, "none"):
        raise ToolError("path: empty; give a file's path relative to the repository root, "
                        "exactly as code_search printed it, such as src/util/retry.ts.")
    root = Path(ctx.repo_root).resolve()
    if rel.startswith("/") or ".." in rel.split("/"):
        raise ToolError(f"path: {path!r} leaves the repository. Paths are relative to the repository "
                        "root, with no leading / and no .., such as src/util/retry.ts.")
    if set(rel.split("/")) & EXCLUDED_DIRS:
        raise ToolError(f"path: {path!r} is under config/, which this tool cannot read. "
                        "Read the code that loads the setting instead; code_search finds it.")
    files = repo_files(root)
    if rel not in files:
        inside = sorted(f for f in files if f.startswith(rel.rstrip("/") + "/"))
        if inside:
            raise ToolError(f"path: {rel} is a directory, not a file. Files in it include: "
                            + ", ".join(inside[:MAX_LISTED]) + ". Pass one of them, exactly as written.")
        near = near_misses(rel, files)
        if near:
            raise ToolError(f"path: no file is at {rel}; paths are case-sensitive. The closest existing "
                            f"paths are: {', '.join(near)}. Call code_read_file again with one of them, "
                            "exactly as written, or find the file with code_search.")
        raise ToolError(f"path: no file is at {rel}, and no path is close to it. Find the file with "
                        "code_search and pass its path exactly as a result prints it.")
    try:
        text = (root / rel).read_text()
    except UnicodeDecodeError:
        raise ToolError(f"path: {rel} is not a text file, so it cannot be read. "
                        "Read the source file that uses it instead.")
    cap = CAPS[response_format]
    return json.dumps({"path": rel, "total_lines": len(text.splitlines()), "total_chars": len(text),
                       "shown_chars": min(len(text), cap), "truncated": len(text) > cap,
                       "content": text[:cap]})

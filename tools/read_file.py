"""read_file: return one file of the private codebase, capped by response_format.

Its definition is read_file.json. It reads the same files search_repo searches:
everything under fixtures/repo except config/. A path is relative to
fixtures/repo, as search results print it. The result is JSON, in the shape
read_file.json's output_schema declares; a file longer than the cap comes back cut,
with "truncated": true.

It asks the file system whether the path exists. On a case-insensitive disk, which
is macOS's default, src/report.ts then opens src/Report.ts and reports success
(Assignment 3, Part B, task 3).
"""

import json
from pathlib import Path

CAPS = {"concise": 6_000, "detailed": 20_000}   # characters returned


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, checked at the boundary."""
    path = args.get("path")
    if isinstance(path, str) and not path.strip():
        return ["path: empty; give a file's path relative to the repository root, "
                "exactly as code_search printed it, such as src/util/retry.ts"]
    return []


def run(ctx, path, response_format="concise"):
    from harness.formats import normalize_path
    from tools import ToolError
    from tools.search_repo import EXCLUDED_DIRS

    rel = normalize_path(str(path))
    if rel in (None, "none"):
        raise ToolError("path is empty")
    root = Path(ctx.repo_root).resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ToolError(f"{path} is outside the repository")
    parts = target.relative_to(root).parts
    if set(parts) & EXCLUDED_DIRS:
        raise ToolError(f"{path} is not readable")
    if not target.is_file():
        raise ToolError(f"no such file: {path}")
    try:
        text = target.read_text()
    except UnicodeDecodeError:
        raise ToolError(f"{path} is not a text file")
    cap = CAPS[response_format]
    return json.dumps({"path": rel, "total_lines": len(text.splitlines()), "total_chars": len(text),
                       "shown_chars": min(len(text), cap), "truncated": len(text) > cap,
                       "content": text[:cap]})

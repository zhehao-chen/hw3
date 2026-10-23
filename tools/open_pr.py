"""open_pr: open a draft pull request on the mock remote. New in Assignment 3.

Its definition is open_pr.json. The mock remote needs no commits: a PR is a branch,
a title and a body, stored in the tracker's database. `idempotency_key` is not in
the definition: your registry passes it, as for post_comment. The result is JSON, in
the shape open_pr.json's output_schema declares.
"""

import json

MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 4_000


def branch_for(number: int) -> str:
    """The one branch a PR for this issue may come from."""
    return f"repomind/issue-{int(number)}"


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, checked at the boundary."""
    out = []
    number = args.get("number")
    has_number = isinstance(number, int) and not isinstance(number, bool)
    if has_number and number <= 0:
        out.append(f"number: {number} is not an issue number; give the positive integer the task names")
    branch = args.get("branch")
    if has_number and number > 0 and isinstance(branch, str) and branch != branch_for(number):
        out.append(f"branch: {branch!r}; for issue {number} it must be exactly {branch_for(number)!r}")
    title = args.get("title")
    if isinstance(title, str):
        if not title.strip():
            out.append("title: empty; give one line saying what the change fixes or adds")
        elif len(title) > MAX_TITLE_CHARS or "\n" in title:
            out.append(f"title: must be one line of at most {MAX_TITLE_CHARS} characters; this one has "
                       f"{len(title)} characters and {title.count(chr(10)) + 1} lines")
    body = args.get("body")
    if isinstance(body, str):
        if not body.strip():
            out.append("body: empty; name the file as \"file: <path>\", then propose the change in prose")
        elif len(body) > MAX_BODY_CHARS:
            out.append(f"body: {len(body):,} characters; the cap is {MAX_BODY_CHARS:,}")
    return out


def run(ctx, number, branch, title, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    try:
        pr = ctx.tracker.open_pr(ctx.run_id, int(number), str(branch), str(title), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"number: issue {number} does not exist in this tracker. Use the issue number "
                        "the task gives you, as a bare integer such as 4417. No pull request was opened.")
    return result_from_row(pr)


def existing(ctx, args: dict, key: str) -> dict | None:
    """The check before every attempt, and the read-back after a timeout: this run's
    open PR from the issue's branch, or None. The branch is a key the PR has by
    nature, so the check finds the PR whatever key opened it. Raises
    ReadbackUnavailable when the tracker cannot be read."""
    rows = ctx.tracker.prs(ctx.run_id, branch=branch_for(args["number"]), state="open")
    return rows[0] if rows else None


def result_from_row(row: dict, replayed: bool | None = None) -> str:
    """The tool's result, from a PR row the tracker wrote or one read back."""
    return json.dumps({"pr": f"pr-{row['id']}", "issue": f"issue-{int(row['issue'])}", "branch": str(row["branch"]),
                       "state": str(row.get("state", "open")), "draft": bool(row.get("draft", 1)),
                       "replayed": bool(row.get("replayed") if replayed is None else replayed)})

"""post_comment: write a comment on an issue. Public: whoever filed the issue can read it.

Its definition is post_comment.json. `idempotency_key` is not in the definition:
the model never sees or sets it. Your registry passes it (Assignment 3, Part C,
task 2), and the tracker accepts each key once. The result is JSON, in the shape
post_comment.json's output_schema declares.
"""

import json

MAX_BODY_CHARS = 4_000


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, checked at the boundary."""
    out = []
    number = args.get("number")
    if isinstance(number, int) and not isinstance(number, bool) and number <= 0:
        out.append(f"number: {number} is not an issue number; give the positive integer the task names")
    body = args.get("body")
    if isinstance(body, str):
        if not body.strip():
            out.append("body: empty; a comment starts with the two lines \"label: ...\" and \"file: ...\"")
        elif len(body) > MAX_BODY_CHARS:
            out.append(f"body: {len(body):,} characters; the cap is {MAX_BODY_CHARS:,}. Shorten the reason")
    return out


def run(ctx, number, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    if not str(body).strip():
        raise ToolError("body: empty; a comment starts with the two lines \"label: ...\" and "
                        "\"file: ...\". Nothing was posted.")
    try:
        comment = ctx.tracker.post_comment(ctx.run_id, int(number), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"number: issue {number} does not exist in this tracker. Use the issue number "
                        "the task gives you, as a bare integer such as 4417. Nothing was posted.")
    return result_from_row(comment)


def existing(ctx, args: dict, key: str) -> dict | None:
    """The check before every attempt, and the read-back after a timeout: this run's
    comment written with this key, or None. Raises ReadbackUnavailable when the
    tracker cannot be read."""
    return ctx.tracker.comment_by_key(ctx.run_id, key)


def result_from_row(row: dict, replayed: bool | None = None) -> str:
    """The tool's result, from a comment row the tracker wrote or one read back."""
    return json.dumps({"comment": f"comment-{row['id']}", "issue": f"issue-{int(row['issue'])}",
                       "chars": len(str(row.get("body", ""))),
                       "replayed": bool(row.get("replayed") if replayed is None else replayed)})

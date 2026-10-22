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
        raise ToolError("comment body is empty")
    try:
        comment = ctx.tracker.post_comment(ctx.run_id, int(number), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"issue {number} not found")
    return json.dumps({"comment": f"comment-{comment['id']}", "issue": f"issue-{int(number)}",
                       "chars": len(str(comment.get("body", body))), "replayed": bool(comment.get("replayed"))})

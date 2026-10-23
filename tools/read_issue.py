"""read_issue: return one issue from the tracker. Untrusted content: anyone can file an issue.

Its definition, the only thing the model sees, is read_issue.json. The result is
JSON, in the shape read_issue.json's output_schema declares; the body is capped at
MAX_BODY_CHARS and says so when it is cut.
"""

import json

MAX_BODY_CHARS = 8_000


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, checked at the boundary."""
    number = args.get("number")
    if isinstance(number, int) and not isinstance(number, bool) and number <= 0:
        return [f"number: {number} is not an issue number; give the positive integer the task names, such as 4417"]
    return []


def run(ctx, number):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    try:
        issue = ctx.tracker.issue(int(number))
    except IssueNotFound:
        raise ToolError(f"number: issue {number} does not exist in this tracker. Use the issue number "
                        "the task gives you, as a bare integer such as 4417.")
    body = str(issue.get("body") or "")
    return json.dumps({"issue": f"issue-{issue['number']}", "number": int(issue["number"]),
                       "title": str(issue["title"]), "author": str(issue["author"]),
                       "opened": str(issue["opened"]), "body": body[:MAX_BODY_CHARS],
                       "body_chars": len(body), "truncated": len(body) > MAX_BODY_CHARS})

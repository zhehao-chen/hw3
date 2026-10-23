# Assignment 3 · A tool surface that tells the truth, and writes that happen once

Fill in each section. Every number here must match `reports/LAB_03.json`, and
every count of writes must come from the tracker's database, not from your traces.

## Part A · The five definitions

- Strict tools, optional parameters, nullable or union-typed parameters (from
  `python tools/audit_schemas.py`): **5 strict tools, 0 optional parameters, 1
  nullable parameter** (`code_search.path_glob`, `["string", "null"]`). The names
  are namespaced by prefix: `tracker_read_issue`, `tracker_post_comment`,
  `code_search`, `code_read_file`, `pr_open_draft`.
- Does your vendor have a strict mode? What did `make ping` show? Yes. The model is
  OpenAI's `gpt-4o-mini`, whose function calling has a strict mode (Structured
  Outputs, sent as `function.strict`). All five definitions set `"strict": true`.
  `make ping` (2026-10-21T19:36:58) sent all five in one request, exactly as
  `registry.schemas` sends them. The request was accepted and served by
  `gpt-4o-mini-2024-07-18` (definitions hash `e8999a4cf6321f0d`), so
  `strict.compiles` is `true`.
- Consolidation, in one line: none found. No task is always the same calls in the
  same order: a search may repeat with new terms before a read, the read depends on
  what the search found, and the two writes stay separate because the PR is skipped
  when the file is `none` and the comment must come last.

`search_repo`, before and after (the full definitions are below the table):

| | Before (`search_repo`) | After (`code_search`) |
|---|---|---|
| Name | `search_repo`: no service named | `code_search`: the codebase's prefix, shared with `code_read_file` |
| Description | "Search the code." | What it matches (line contents, case-insensitive, never file names or `config/`), when to use it, what it costs, the result's shape, the cap, what an empty result means, and what counts as malformed |
| Query | `query`: "Text to look for" | `query`: 1 to 200 characters of literal text; no operators; says why a short distinctive term works and a sentence does not |
| Filter | `filter`: "Optional filter expression" (free text) | `path_glob`: a glob or `null`, with its character set and examples; checked by `glob_problem` at the boundary |
| Depth | `deep`: boolean, "Whether to do a deep search" | `source`: enum `index` / `live_files`, saying what each reads, what it costs and when to choose it |
| Size | `max_results`: integer, "Default 10" | Removed. `response_format`: enum `concise` (10 lines × 120 characters, the default) / `detailed` (20 × 300) |
| Optional parameters | 3 (`max_results`, `filter`, `deep`) | 0: all four required, `path_glob` nullable |
| Result | Plain text lines, then a footer line | JSON: `query`, `path_glob` echoed, `query_valid`, `count`, `shown`, `truncated`, `source`, `index_age`, `results[{path, line, text}]` |
| No match | `"No matches."` | `count: 0`, `results: []`, `query_valid: true`: a success |
| Malformed query | `"No matches."`, the same as no match | An `is_error` result naming the field and what would be right; nothing is searched |
| Examples | None | Two: a typical identifier search, then a phrase narrowed with a glob and `detailed` |
| Strict | Not set; no `additionalProperties: false` | `"strict": true`; `additionalProperties: false` |

<details><summary>Before: <code>tools/search_repo.json</code> at <code>8f65af6</code></summary>

```json
{
  "name": "search_repo",
  "description": "Search the code.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Text to look for"},
      "max_results": {"type": "integer", "description": "How many results. Default 10."},
      "filter": {"type": "string", "description": "Optional filter expression"},
      "deep": {"type": "boolean", "description": "Whether to do a deep search. Default false."}
    },
    "required": ["query"]
  }
}
```
</details>

<details><summary>After: <code>tools/search_repo.json</code> (input schema and examples; the file also declares an <code>output_schema</code>, which the registry checks every result against and never sends to the vendor)</summary>

```json
{
  "name": "code_search",
  "description": "Search Ledgerline's codebase for a literal piece of text, case-insensitively, line by line. It matches the contents of lines, never file names, and never the config/ directory. Use it to find which files contain an error message, an identifier or a word from the issue, then read the most likely file with code_read_file. It costs one local read and no money; each call costs a turn, so search for one distinctive term at a time rather than a sentence.\n\nReturns JSON: {\"query\": <your query, echoed>, \"path_glob\": <your filter, echoed>, \"query_valid\": true, \"count\": <every matching line>, \"shown\": <lines in results>, \"truncated\": <true when count > shown>, \"source\": \"index\" or \"live_files\", \"index_age\": <how old the index is, e.g. \"22 days ago\">, \"results\": [{\"path\": \"src/api/uploads.ts\", \"line\": 42, \"text\": ...}]}, in path order. A response is capped: concise shows at most 10 matching lines, each cut to 120 characters; detailed shows at most 20, each cut to 300 characters. When \"truncated\" is true, narrow the query or set path_glob rather than asking again.\n\nNo match is a success: \"count\": 0, \"results\": [], \"query_valid\": true means the text appears nowhere in the searched files; try a different term. A malformed search is an error, never an empty result: a query that is empty or only whitespace, a query longer than 200 characters, or a path_glob that is not a valid glob.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "The literal text to find, 1 to 200 characters, matched case-insensitively as a substring of one line. No operators: AND, OR, quotes, wildcards and regular expressions are searched for as literal characters. A short distinctive term (an identifier such as withRetry, or a phrase from an error message) finds the responsible line; a long sentence usually finds nothing."},
      "path_glob": {"type": ["string", "null"], "description": "null searches every file. A glob relative to the repository root, such as \"src/**/*.ts\" or \"src/{api,db}/*.ts\", restricts the search to matching files: choose it when a query matches too many lines (\"truncated\": true) or the issue names an area of the code. It uses letters, digits, / . _ - and the wildcards * ? [ ] { }; it cannot start with / or contain .."},
      "source": {"type": "string", "enum": ["index", "live_files"], "description": "index, the normal choice, searches the prebuilt index, which is fast but as old as its index_age. live_files reads every file from disk instead: slower, and needed only when the code may have changed since the index was built, or when an index search found nothing that the issue says must exist."},
      "response_format": {"type": "string", "enum": ["concise", "detailed"], "description": "concise, the cheap default, returns at most 10 matching lines of 120 characters: enough to choose a file. detailed returns at most 20 lines of 300 characters and costs about four times the tokens; choose it only when concise lines were cut before the part you need."}
    },
    "required": ["query", "path_glob", "source", "response_format"],
    "additionalProperties": false
  },
  "strict": true,
  "examples": [
    {"input": {"query": "withRetry", "path_glob": null, "source": "index", "response_format": "concise"},
     "note": "The typical call: one identifier, the whole codebase, the index, concise."},
    {"input": {"query": "upload failed", "path_glob": "src/api/*.ts", "source": "index", "response_format": "detailed"},
     "note": "A hard case: a phrase from an error message that matched too many lines, narrowed to one directory with a glob (not a filter expression such as dir:src/api), with detailed lines because the concise ones were cut."}
  ]
}
```
</details>

The boundary check (`tools/__init__.py`, `ToolRegistry.call`) runs every call through
five stages, in order: protocol (a known tool, arguments that are a JSON object),
the input schema (after enum values are matched case-insensitively), the tool
module's `check()` (the lengths, ranges and formats that strict mode keeps out of a
schema), the tool's `run()`, and the result against the `output_schema` its
description promises. A call that fails any of the first three never runs the tool.

The first dev run with the new names found two failures, both fixed before the
next run. First, `gpt-4o-mini` wrote its own namespace into the plan's tool names
(`functions.tracker_post_comment`), so three of four plans failed the "comment
last" check and the runs escalated. The planner prompt now forbids the prefix, and
`agent/plan.py` strips it as a vendor artifact. Second, the planner pre-wrote the
comment's body around `$fill` (`"label: <bug | …>\nfile: <…>\n\n$fill"`), the
executor treated that body as fixed, and the template was posted verbatim. The
prompt now requires the body to be exactly `"$fill"`, and the executor
(`needs_fill` in `ways/planned.py`) treats any argument holding `$fill` as unfilled.

## Part B · Errors

| Tool | Transient | Permanent | Ambiguous |
|---|---|---|---|
| `tracker_read_issue` | The tracker's database is locked or busy | The issue does not exist; the number is not a positive integer; an argument is missing, unknown or of the wrong type | None: it only reads |
| `code_search` | An I/O error reading the index or a file | The query is empty, only whitespace or over 200 characters; `path_glob` is not a valid glob; an argument is missing, unknown or of the wrong type, or an enum value is outside its set | None: it only reads |
| `code_read_file` | An I/O error reading the file | No file at the path, including one that differs only in case; the path is a directory, under `config/`, or leaves the repository; the file is not text | None: it only reads |
| `tracker_post_comment` | The database is locked before the write commits, so nothing was written | The issue does not exist; the body is empty or over 4,000 characters | The write times out after it was dispatched (`TrackerTimeout`): the comment may have been posted |
| `pr_open_draft` | The database is locked before the write commits, so nothing was written | The issue does not exist; the branch is not `repomind/issue-<number>`; the title is empty, multi-line or over 120 characters; the body is empty or over 4,000 characters | The write times out after it was dispatched (`TrackerTimeout`): the PR may have been opened |

Where each class is handled:

- **Transient.** Retried in code, below the model, in `ToolRegistry._run`: a read that
  hits sqlite's `database is locked` is retried at most 3 times, with 0.2 s and then
  0.4 s of backoff. Those retries cost no turn. If the third attempt also fails,
  the model gets an error that says a retry can work ("Retry the same call after 5
  seconds; it can succeed."). A write is never retried here, because a write that
  failed may have happened. Part C handles writes.
- **Permanent.** Sent to the model as an `is_error` result. The repair fact comes
  first: the field at fault, what is wrong, and what would be right. The message
  ends with "Calling again with the same arguments will not succeed." Errors are
  capped at 600 characters. The boundary rejects malformed arguments before the
  tool runs. The tool raises `ToolError` for failures that only running it can
  find.
- **Ambiguous.** Never retried blind. Part C's gate checks for the effect before
  every attempt, reads it back after a timeout, and escalates when the read-back
  cannot tell.

- Protocol errors: **`generic_result`**. An unknown tool name, or arguments that
  are not a JSON object (including the `_unparsed_arguments` and `{"value": ...}`
  wrappers `harness/backends.py` puts around them), is written in full to
  `runs/<run_id>.harness.log`. The model gets a 149-character result that names no
  tool and says that retrying will not help: "Error: the harness could not run
  this call. It is not a problem with your argument values; do not retry it.
  Continue with the tools you were offered." Why: in this harness a protocol error
  is almost always a model inventing a tool name or emitting a cut-off call. It
  still has five real tools and a task it can finish. Ending the run would turn
  one stray token into a failed triage. The generic result answers the call, as
  the API requires, and gives the model nothing to argue with. A fault inside a
  tool (an exception other than `ToolError`) is different: it is logged and
  re-raised, and the run ends `tool_error`.
- The exact text of your `read_file` near-miss error for `src/report.ts`:

```
Error: path: no file is at src/report.ts; paths are case-sensitive. The closest existing paths are: src/Report.ts, src/report.tsx, src/reports.ts. Call code_read_file again with one of them, exactly as written, or find the file with code_search. Calling again with the same arguments will not succeed.
```

`code_read_file` does not ask the file system whether a path exists. On macOS's
case-insensitive disk, the starter's version opened `src/Report.ts` for
`src/report.ts`, and returned it as a success under the wrong name. The tool now
compares the path with the repository's own file list, in its real case. It ranks
near misses in this order: the same path in another case, the same name with
another extension, the same file name in another directory, then a path within a
character or two.

## Part C · Writes that may already have happened

- The key formula (`write_key`, `tools/__init__.py:339`): the harness computes it,
  and the model never sees or sets it.

  ```python
  key = sha256(json.dumps({"run_id": run_id, "plan_step": plan_step, "args": args},
                          sort_keys=True, separators=(",", ":"))).hexdigest()[:32]
  ```

  `args` are the arguments after the boundary check, without the key. The registry
  computes the key once per call and sends the same key and the same arguments on
  every attempt, so a retry can never re-derive them. Under `KEY_MODE=uuid` it
  sends `uuid4().hex`, fresh on every attempt, instead.
- How a run whose file is `none` skips the PR: **by a check in the executor**
  (`Executor.open_pr` in `ways/planned.py`). The plan always carries the PR step
  right before the comment. `schema_check` requires exactly one comment step, the
  last, with exactly one PR step right before it. When the executor first reaches
  either step, it drafts the comment and runs the second pass. A draft the second
  pass holds ends the run before either write. At the PR step, the executor reads
  the draft's `file:` line. If it is `none`, the step does not run: it gets no
  `step_results` entry, and the trace gets a `pr_skipped` event. Otherwise the
  harness sets the PR's number and branch (`repomind/issue-<n>`) from the issue,
  and the model fills the title and body, shown the drafted file. The body is made
  to start with `file: <path>`. A PR that fails to open ends the run before the
  comment. Tested without tokens with `harness.scripted:DraftNamesNoFile`: both dev
  issues posted one `file: none` comment and opened no PR. Under the real model it
  also happened twice, when `gpt-4o-mini` drafted `file: none` for issue 4192.
- How a write is made safe to repeat (`ToolRegistry._write`). Before every
  attempt, one read: an open PR from `repomind/issue-<n>` in this run, or a comment
  with this key. When it exists, the call returns it and writes nothing. After a
  `TrackerTimeout`, the attempt is recorded `timeout` and the write is read back.
  Found: go on with it. Not found: retry with the same key. `ReadbackUnavailable`:
  raise `WriteUnconfirmed`, and the executor ends the run `escalated`. At most 3
  attempts; a write still unconfirmed after the third escalates too. `VERIFY=off`
  skips only the read-back; the check before each attempt and the bound stay.
- One call-record line for a write. Issue 4192, run `planned-4192-s02-cca6c1`
  under `FLAKY_WRITES`: the comment's first attempt timed out and its read-back
  found nothing (the tracker logged `timeout_before_commit`). The second attempt,
  with **the same key**, committed and timed out too, and its read-back found it.
  These are the three lines for the comment:

```json
{"run_id": "planned-4192-s02-cca6c1", "t": 27.925, "tool": "tracker_post_comment", "role": "comment", "args": {"number": 4192, "body": "label: bug\nfile: src/api/uploads.ts"}, "plan_step": 5, "key": "879c182f0f08e64d10ad50d7190189d5", "attempt": 1, "outcome": "timeout", "is_error": true, "result": "timed out: the tracker did not answer in time; the write may have happened", "latency_s": 0.001, "cost_usd": 0.0}
{"run_id": "planned-4192-s02-cca6c1", "t": 27.927, "tool": "tracker_post_comment", "role": "comment", "args": {"number": 4192, "body": "label: bug\nfile: src/api/uploads.ts"}, "plan_step": 5, "key": "879c182f0f08e64d10ad50d7190189d5", "attempt": 2, "outcome": "timeout", "is_error": true, "result": "timed out: the tracker did not answer in time; the write may have happened", "latency_s": 0.003, "cost_usd": 0.0}
{"run_id": "planned-4192-s02-cca6c1", "t": 27.929, "tool": "tracker_post_comment", "role": "comment", "args": {"number": 4192, "body": "label: bug\nfile: src/api/uploads.ts"}, "plan_step": 5, "key": "879c182f0f08e64d10ad50d7190189d5", "attempt": 2, "outcome": "ok", "is_error": false, "result": "{\"comment\": \"comment-12\", \"issue\": \"issue-4192\", \"chars\": 35, \"replayed\": false}", "latency_s": 0.005, "cost_usd": 0.0}
```

The tracker holds one comment for that run. Its `attempts` table logs
`timeout_before_commit`, then `timeout_after_commit`, both with this key.

Part C's development tests, with `gpt-4o-mini` on the two development issues:

| Switches | Runs | Ended | Comments per run | PRs per run | Duplicates |
|---|---|---|---|---|---|
| `FLAKY_WRITES=1` | 6 | 6 completed | 1 in every run | 1 in every run | 0 |
| `FLAKY_WRITES=1 READBACK_FAIL=1` | 6 | 6 escalated | 0 in the 4 runs whose PR timed out; 1 in the 2 runs whose draft named `none` | 1 in the 4 runs whose PR timed out; 0 in the other 2 | 0 |

Under `READBACK_FAIL`, each of the four runs whose PR committed and timed out ended
`escalated` before the comment. Each left the one PR that committed and no comment.
These runs are tests, not the measurement.

## Part D · Measure

1. The surface price, with its arithmetic: the five definitions together, each alone, and the
   planner's tool list, the starter's and yours; then a mean harness-arm run, each call
   charged for what it carried, and the same calls with all five on every one:

2. The table, from the tracker:

| Measure | Your harness | Control |
|---|---|---|
| Triage comments per run (should be 1) | | |
| Open PRs from `repomind/issue-<n>` per run (1 when the file is not `none`, else 0) | | |
| Writes that timed out and had in fact committed | | |
| Runs that ended `escalated` | | |
| Triage success (your grounded check from Assignment 1) | | |

3. The four shapes of tool misuse: which your surface narrowed, with evidence for
   each; which is still standing; did you see it in your runs, and how would you
   know if you had not?

4. Every prediction in `reports/PREDICTIONS_03.md` that missed, and why:

## AI assistance

Which tool, what it wrote, and what you checked:

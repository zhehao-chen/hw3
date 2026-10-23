"""RepoMind's tools, and the registry that runs them.

Each tool is two files with one name: tools/<module>.json, its definition, the only
thing the model sees of it; and tools/<module>.py, the code that runs it. Rename a
tool inside its .json file and keep both file names. tools/roles.yaml says which
tool plays each of RepoMind's five roles (checks/FORMATS.md, section 11.2).

Assignment 3 starts with five definitions that are weak on purpose: generic names,
free-text filters, nothing said about an empty result, no cap on a response, no
examples. You rewrite them (Part A), and you make this registry check every call
at the boundary, return errors a model can repair from, key every write, verify a
write that failed, record every call and run a hook before each one (Parts A to C).

The registry's interface, which the runner, your ways and the checker use
(checks/FORMATS.md, section 11.3):

    registry = ToolRegistry(ctx)          # ctx is a RunContext, below
    registry.schemas                      # what the vendor gets: name, description, input_schema, strict
    registry.names()                      # the tools' names, in MODULES order
    registry.definition(name)             # the whole definition file, examples included
    registry.hooks.append(hook)           # hook(name, tool_input) runs before every call
    registry.call(name, tool_input, plan_step=None) -> ToolResult

A registry is bound to one run: every write is stamped with that run's ID. Build
one per run with `default_tools(ctx)`.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

TOOLS_DIR = Path(__file__).resolve().parent

# The five modules, in the order the registry offers them. Keep these file names.
MODULES = ("read_issue", "search_repo", "read_file", "post_comment", "open_pr")

# The fields of a definition the vendor gets. Everything else in the file (the
# examples, notes of your own) stays here.
SENT = ("name", "description", "input_schema", "strict")


class ToolError(Exception):
    """A tool failed in a way the model can be told about. Becomes an is_error result.

    The message is the repair fact: what was wrong and what would be right, first.
    `retry` says whether calling again can work; most tool failures are permanent,
    so the default tells the model that the same call will fail again."""

    def __init__(self, message: str, *, retry: str | None = None):
        super().__init__(message)
        self.retry = retry or NO_RETRY


# What a permanent failure tells the model about calling again.
NO_RETRY = "Calling again with the same arguments will not succeed."


@dataclass
class ToolResult:
    content: str
    is_error: bool
    latency_s: float


@dataclass
class RunContext:
    run_id: str
    tracker: object                    # harness.tracker.Tracker
    repo_root: object                  # pathlib.Path to fixtures/repo
    calls: object = None               # harness.calls.CallRecord: runs/<run_id>.calls.jsonl
    harness_log: object = None         # pathlib.Path: runs/<run_id>.harness.log, for protocol errors
    switches: dict = field(default_factory=dict)   # harness.switches.current(), as the runner read them


def load_definition(module: str) -> dict:
    return json.loads((TOOLS_DIR / f"{module}.json").read_text())


class ToolRegistry:
    def __init__(self, ctx: RunContext, modules=MODULES):
        self.ctx = ctx
        self.hooks: list = [allow_every_call]   # each hook(name, tool_input) runs before every call
        self._tools: dict[str, tuple[dict, ModuleType]] = {}
        for module in modules:
            definition = load_definition(module)
            self._tools[definition["name"]] = (definition, importlib.import_module(f"tools.{module}"))

    @property
    def schemas(self) -> list[dict]:
        """The definitions as the vendor gets them, in MODULES order, ready for
        ModelClient.create(tools=...): the SENT fields only, with the examples rendered
        into the end of the description, which every vendor reads."""
        out = []
        for d, _ in self._tools.values():
            sent = {k: d[k] for k in SENT if k in d}
            if d.get("examples"):
                sent["description"] = f"{d.get('description', '')}\n\n{render_examples(d['examples'])}"
            out.append(sent)
        return out

    def names(self) -> list[str]:
        return list(self._tools)

    def definition(self, name: str) -> dict:
        return self._tools[name][0]

    def call(self, name: str, tool_input: dict, plan_step=None) -> ToolResult:
        """Run one tool through the boundary. In order:

          1. protocol: a tool by that name, and arguments that are an object. A failure
             is for the harness, not the model: written in full to the harness log, and
             answered with a generic result that names no tool (GENERIC_PROTOCOL_ERROR);
          2. schema: the arguments against the tool's input_schema, after enum values
             are matched case-insensitively;
          3. content: the tool module's check(), the rules strict mode keeps out of a
             schema (lengths, ranges, formats);
          4. the tool's run(); a ToolError comes back as an is_error result;
          5. the result against the tool's output_schema, before the model sees it.

        Before stage 2, every hook in `self.hooks` may deny the call (fail closed: a
        hook that raises denies it). A call that is denied or fails 1 to 3 never runs
        the tool, so it writes nothing. Every attempt leaves one line in the call
        record (runs/<run_id>.calls.jsonl), whatever happened to it.

        `plan_step` is the id of the plan step the call runs for, from ctx.call_tool,
        or None when the model chose the call (the agent's loop)."""
        start = time.monotonic()
        args = tool_input
        attempt = {"key": None, "n": 1}       # a write's key and attempt, for its call-record lines

        def done(content: str, is_error: bool, outcome: str) -> ToolResult:
            result = ToolResult(content, is_error, time.monotonic() - start)
            self._record(name, args, plan_step, outcome, result, key=attempt["key"], attempt=attempt["n"])
            return result

        # 1. protocol
        entry = self._tools.get(name)
        if entry is None:
            self._log_protocol(f"unknown tool {name!r}; the registry has {self.names()}; input {tool_input!r}")
            return done(GENERIC_PROTOCOL_ERROR, True, "protocol")
        if not isinstance(tool_input, dict) or set(tool_input) & WRAPPED_INPUT:
            self._log_protocol(f"{name}: arguments are not a JSON object: {tool_input!r}")
            args = {"_not_an_object": repr(tool_input)[:200]}
            return done(GENERIC_PROTOCOL_ERROR, True, "protocol")
        definition, module = entry
        schema = definition.get("input_schema") or {}

        # the hooks, before anything about the arguments is trusted
        reason = self._denied(name, tool_input)
        if reason is not None:
            return done(cap(f"Error: {name} was denied by a policy check and did not run: {reason}. "
                            "Do not retry it."), True, "denied")

        # 2. schema
        tool_input = args = normalize_enums(tool_input, schema)
        problems = schema_errors(tool_input, schema)
        # 3. content, only on arguments whose structure is right
        if not problems and hasattr(module, "check"):
            problems = module.check(tool_input)
        if problems:
            return done(rejection(name, problems), True, "rejected")

        # 4. run: transient failures retried here, below the model; a ToolError goes to it.
        #    A write goes through the gate: its key, the check before every attempt, the
        #    read-back after a timeout, at most WRITE_ATTEMPTS attempts.
        try:
            if is_write(module):
                content = self._write(name, module, tool_input, plan_step, start, attempt)
            else:
                content = self._run(name, module, tool_input)
        except WriteUnconfirmed:
            raise                                # the third exit: the executor ends the run escalated
        except ToolError as exc:
            return done(cap(f"Error: {exc} {exc.retry}"), True, "error")
        except Exception as exc:                 # a fault in the tool: for you, and the run ends
            self._log_protocol(f"{name}: the tool raised {type(exc).__name__}: {exc}; input {tool_input!r}")
            self._record(name, args, plan_step, "error",
                         ToolResult(f"{type(exc).__name__}: {exc}", True, time.monotonic() - start))
            raise

        # 5. the result's shape
        broken = result_errors(content, definition.get("output_schema"))
        if broken:
            self._log_protocol(f"{name}: result does not fit its output_schema: {broken}; result {content[:500]!r}")
            return done(f"Error: {name} returned a result in an unexpected shape. This is a fault in the tool, "
                        f"not in your arguments; do not retry with other arguments.", True, "error")
        return done(content, False, "ok")

    def _denied(self, name: str, tool_input: dict) -> str | None:
        """Run every hook. The reason the first one that denies gives, or None when all
        allow. Fail closed: a hook that raises denies the call, and the error is logged."""
        for hook in list(self.hooks):
            try:
                verdict = hook(name, tool_input)
            except Exception as exc:
                self._log_protocol(f"{name}: hook {getattr(hook, '__name__', hook)!r} raised "
                                   f"{type(exc).__name__}: {exc}; the call is denied")
                return "a policy check failed to run, so the call is denied"
            if verdict is not None:
                return str(verdict) or "denied"
        return None

    def _record(self, name: str, args, plan_step, outcome: str, result: ToolResult,
                key: str | None = None, attempt: int = 1) -> None:
        """One line in runs/<run_id>.calls.jsonl, written here, at the boundary, whatever
        the agent later says it did."""
        calls = getattr(self.ctx, "calls", None)
        if calls is None:
            return
        calls.write(tool=name, args=args if isinstance(args, dict) else {"_not_an_object": repr(args)[:200]},
                    plan_step=plan_step, key=key, attempt=attempt, outcome=outcome, is_error=result.is_error,
                    result=result.content, latency_s=result.latency_s, cost_usd=0.0)

    def _write(self, name: str, module: ModuleType, args: dict, plan_step, start: float, attempt: dict) -> str:
        """A write that may already have happened, made safe to repeat (Part C):

          - the key is the harness's, never the model's: a hash of the run, the plan
            step and the arguments (write_key), the same on every attempt; under
            KEY_MODE=uuid, a fresh random key on every attempt instead (the control);
          - before every attempt, one read: has this write already happened? If so,
            its existing result is returned and nothing is written;
          - after a timeout, the write is read back (skipped under VERIFY=off). Found:
            go on with it. Not found: retry with the same key. Cannot tell
            (ReadbackUnavailable): raise WriteUnconfirmed, and the run ends escalated;
          - at most WRITE_ATTEMPTS attempts; one still unconfirmed after the last
            raises WriteUnconfirmed too.

        Every timeout leaves a call-record line with outcome "timeout"; the attempt
        that settles the write is recorded by call(), with the same key and attempt."""
        from harness.tracker import ReadbackUnavailable, TrackerTimeout

        switches = getattr(self.ctx, "switches", None) or {}
        uuid_keys = switches.get("key_mode") == "uuid"
        verify = switches.get("verify", "on") != "off"
        derived = write_key(self.ctx.run_id, plan_step, args)

        def look() -> dict | None:
            try:
                return module.existing(self.ctx, args, attempt["key"])
            except ReadbackUnavailable as exc:
                self._unconfirmed(name, args, plan_step, start, attempt,
                                  f"the tracker could not be read to check whether the write happened ({exc})")

        for n in range(1, WRITE_ATTEMPTS + 1):
            attempt["n"], attempt["key"] = n, (uuid.uuid4().hex if uuid_keys else derived)
            found = look()                                   # check before every attempt
            if found is not None:                            # replayed only if it predates this call
                return module.result_from_row(found, replayed=n == 1)
            try:
                return self._run(name, module, {**args, "idempotency_key": attempt["key"]})
            except TrackerTimeout as exc:
                self._record(name, args, plan_step, "timeout",
                             ToolResult(f"timed out: {exc}; the write may have happened", True,
                                        time.monotonic() - start), key=attempt["key"], attempt=n)
                if not verify:
                    continue                                 # the control: retry blind
                found = look()                               # verify: did the effect happen?
                if found is not None:                        # it did: this call's own write
                    return module.result_from_row(found, replayed=False)
        self._unconfirmed(name, args, plan_step, start, attempt,
                          f"still unconfirmed after {WRITE_ATTEMPTS} attempts")

    def _unconfirmed(self, name, args, plan_step, start, attempt, why: str):
        """The third exit: the write cannot be confirmed, so a person must look. Logged,
        recorded, and raised for the executor, which ends the run escalated."""
        message = f"{name}: {why}; key {attempt['key']}"
        self._log_protocol(f"escalate {message}")
        self._record(name, args, plan_step, "error", ToolResult(f"unconfirmed: {why}", True, time.monotonic() - start),
                     key=attempt["key"], attempt=attempt["n"])
        raise WriteUnconfirmed(message)

    def _run(self, name: str, module: ModuleType, tool_input: dict) -> str:
        """The tool's run(), with a read retried in code when the tracker's database is
        busy (sqlite's "database is locked", when runs share it): a transient failure,
        retried at most TRANSIENT_TRIES times with backoff, which costs no turn. A
        write is never retried here: a write that failed may have happened (Part C)."""
        import sqlite3

        for attempt in range(1, TRANSIENT_TRIES + 1):
            try:
                return module.run(self.ctx, **tool_input)
            except sqlite3.OperationalError as exc:
                self._log_protocol(f"{name}: transient {exc} on attempt {attempt}")
                if is_write(module) or attempt == TRANSIENT_TRIES:
                    raise ToolError("the tracker was busy and did not answer.",
                                    retry="Retry the same call after 5 seconds; it can succeed.") from exc
                time.sleep(TRANSIENT_BACKOFF_S * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    def _log_protocol(self, line: str) -> None:
        """Write a protocol error in full to runs/<run_id>.harness.log: it is for you, not the model."""
        path = getattr(self.ctx, "harness_log", None)
        if not path:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as log:
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} protocol {line}\n")


# -- the boundary ----------------------------------------------------------------------

# What the model gets for a protocol error: generic, under 200 characters, naming no tool.
GENERIC_PROTOCOL_ERROR = ("Error: the harness could not run this call. It is not a problem with your "
                          "argument values; do not retry it. Continue with the tools you were offered.")

# How harness/backends.py wraps arguments that were not a JSON object.
WRAPPED_INPUT = {"_unparsed_arguments", "value"}

MAX_ERROR_CHARS = 600

# Transient failures, retried in code below the model (Part B, task 4).
TRANSIENT_TRIES = 3
TRANSIENT_BACKOFF_S = 0.2

# The write tools' modules: never retried blind (Part C).
WRITE_MODULES = ("post_comment", "open_pr")

# A write makes at most this many attempts, in both of Part D's arms (Part C, task 4).
WRITE_ATTEMPTS = 3


class WriteUnconfirmed(Exception):
    """A write whose effect cannot be confirmed: the read-back failed, or the attempts
    ran out. Your code, not the model, stops the run: the executor ends it escalated."""


def is_write(module: ModuleType) -> bool:
    return module.__name__.rsplit(".", 1)[-1] in WRITE_MODULES


def write_key(run_id: str, plan_step, args: dict) -> str:
    """The idempotency key, computed by the harness, never by the model: a hash of the
    run, the plan step and the arguments in canonical form (keys sorted, no
    whitespace). The same intended write gets the same key on every attempt."""
    canonical = json.dumps({"run_id": run_id, "plan_step": plan_step, "args": args},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def cap(text: str) -> str:
    """An error, cut to MAX_ERROR_CHARS; its repair fact comes first, so a cut one still says what to do."""
    return text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS - 3] + "..."


def allow_every_call(name: str, tool_input: dict) -> str | None:
    """RepoMind's own pre-call hook. It allows every call (returns None); a hook that
    returns a reason denies the call, and one that raises denies it too."""
    return None


def render_examples(examples: list[dict]) -> str:
    """The definition file's examples, as the end of the description."""
    lines = ["Examples:"]
    for i, example in enumerate(examples, 1):
        lines.append(f"{i}. {json.dumps(example.get('input', {}), ensure_ascii=False)}")
        if example.get("note"):
            lines.append(f"   {example['note']}")
    return "\n".join(lines)


def normalize_enums(tool_input: dict, schema: dict) -> dict:
    """Top-level enum values matched case-insensitively: "Concise" becomes "concise"."""
    out = dict(tool_input)
    for name, prop in (schema.get("properties") or {}).items():
        value, allowed = out.get(name), (prop or {}).get("enum")
        if isinstance(value, str) and allowed and value not in allowed:
            match = next((a for a in allowed if isinstance(a, str) and a.lower() == value.strip().lower()), None)
            if match is not None:
                out[name] = match
    return out


def schema_errors(tool_input: dict, schema: dict) -> list[str]:
    """The arguments against the input_schema, one line per field at fault, each saying
    what would be right."""
    from harness.schema import errors

    props = schema.get("properties") or {}
    out = []
    for line in errors(tool_input, schema):
        field_name = line.split(":", 1)[0].split(".", 1)[0].split("[", 1)[0]
        if field_name in props and line.endswith("required, and missing"):
            prop = props[field_name]
            want = f"one of {prop['enum']}" if prop.get("enum") else " or ".join(_as_list(prop.get("type")))
            line = f"{line}; want {want}"
        elif "not in the schema" in line:
            line = f"{line}; the arguments are {sorted(props)}"
        out.append(line)
    return out


def rejection(name: str, problems: list[str]) -> str:
    """A boundary rejection: every field at fault first, then that nothing ran. Capped."""
    return cap(f"Error: {name} was not run; fix these arguments and call it again: "
               + "; ".join(problems) + ". " + NO_RETRY)


def result_errors(content, output_schema: dict | None) -> list[str]:
    """The tool's result against its declared output_schema; [] when it fits or none is declared."""
    if not output_schema:
        return []
    from harness.schema import errors

    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return ["the result is not JSON"]
    return errors(value, output_schema)


def _as_list(kinds) -> list:
    return kinds if isinstance(kinds, list) else [kinds] if kinds else []


def default_tools(ctx: RunContext) -> ToolRegistry:
    return ToolRegistry(ctx)

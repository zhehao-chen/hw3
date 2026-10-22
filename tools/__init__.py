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

import importlib
import json
import time
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
    """A tool failed in a way the model can be told about. Becomes an is_error result."""


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
        self.hooks: list = []
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

        A call that fails 1 to 3 never runs the tool, so it writes nothing.

        `plan_step` is the id of the plan step the call runs for, from ctx.call_tool,
        or None when the model chose the call (the agent's loop)."""
        start = time.monotonic()

        def done(content: str, is_error: bool) -> ToolResult:
            return ToolResult(content, is_error, time.monotonic() - start)

        # 1. protocol
        entry = self._tools.get(name)
        if entry is None:
            self._log_protocol(f"unknown tool {name!r}; the registry has {self.names()}; input {tool_input!r}")
            return done(GENERIC_PROTOCOL_ERROR, True)
        if not isinstance(tool_input, dict) or set(tool_input) & WRAPPED_INPUT:
            self._log_protocol(f"{name}: arguments are not a JSON object: {tool_input!r}")
            return done(GENERIC_PROTOCOL_ERROR, True)
        definition, module = entry
        schema = definition.get("input_schema") or {}

        # 2. schema
        tool_input = normalize_enums(tool_input, schema)
        problems = schema_errors(tool_input, schema)
        # 3. content, only on arguments whose structure is right
        if not problems and hasattr(module, "check"):
            problems = module.check(tool_input)
        if problems:
            return done(rejection(name, problems), True)

        # 4. run
        try:
            content = module.run(self.ctx, **tool_input)
        except ToolError as exc:
            return done(f"Error: {exc}", True)

        # 5. the result's shape
        broken = result_errors(content, definition.get("output_schema"))
        if broken:
            self._log_protocol(f"{name}: result does not fit its output_schema: {broken}; result {content[:500]!r}")
            return done(f"Error: {name} returned a result in an unexpected shape. This is a fault in the tool, "
                        f"not in your arguments; do not retry with other arguments.", True)
        return done(content, False)

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
    text = (f"Error: {name} was not run; fix these arguments and call it again: "
            + "; ".join(problems) + ".")
    return text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS - 3] + "..."


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

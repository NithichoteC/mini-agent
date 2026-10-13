"""The events a run emits, one flat JSON object each with a "type".

    session.start   a new session began            session, task, model, workspace
    step            one recorded step (TraceStep)  session, run, step_id, + the TraceStep fields
    file.changed    a tool changed the workspace   session, kind (write|delete|move|mkdir), path, dest?
    approval        a tool waits for a human       id, tool, args, title, always        (backend)
    session.end     the turn is over               status, answer, total_tokens, trace_id, ...  (launcher)
    error           the turn could not run         message                               (backend)

Names follow our own dotted style; AG_UI maps each one onto the AG-UI protocol's event types
(github.com/ag-ui-protocol/ag-ui), the closest public standard for agent -> UI streams, so a
frontend built for AG-UI can be served by a thin adapter. validate() is what the tests run on
every event a scripted session emits.
"""
from dataclasses import dataclass, field, fields

# type -> fields every event of that type must carry
EVENTS = {
    "session.start": ("session", "task", "model", "workspace"),
    "step": ("session", "run", "step_id"),
    "file.changed": ("session", "kind", "path"),
    "approval": ("id", "tool", "args", "title"),
    "session.end": ("status", "answer", "total_tokens", "trace_id"),
    "error": ("message",),
}

AG_UI = {
    "session.start": "RUN_STARTED",
    "step": "STEP_FINISHED",          # a tool step also maps to TOOL_CALL_START/ARGS/END + TOOL_CALL_RESULT
    "file.changed": "CUSTOM",         # name "file.changed"
    "approval": "CUSTOM",             # name "approval"; answered over POST, not over the stream
    "session.end": "RUN_FINISHED",
    "error": "RUN_ERROR",
}

FILE_CHANGES = ("write", "delete", "move", "mkdir")


@dataclass
class TraceStep:
    """One row of the trace (tracing/tracedb.py `steps`): enough to replay what the agent saw and did.
    tool is None for a plain model step such as the review; decision is the permission layer's label
    (allow, ask_yes, ask_always, ask_no, deny, invalid, final_answer)."""
    run: int
    step_id: str
    model: str | None = None
    tool: str | None = None
    args: dict | None = None
    decision: str | None = None
    ok: bool | None = None
    model_output: str | None = None
    observation: str | None = None
    duration_ms: int | None = None
    tokens: int = 0
    extra: dict = field(default_factory=dict)

    @classmethod
    def names(cls) -> set[str]:
        return {f.name for f in fields(cls)} - {"extra"}


def validate(event: dict) -> dict:
    """The event itself when it has a known type and that type's fields; ValueError otherwise."""
    if not isinstance(event, dict) or event.get("type") not in EVENTS:
        raise ValueError(f"unknown event type: {event.get('type') if isinstance(event, dict) else event!r}")
    missing = [f for f in EVENTS[event["type"]] if f not in event]
    if missing:
        raise ValueError(f"{event['type']} event without {', '.join(missing)}")
    if event["type"] == "file.changed" and event["kind"] not in FILE_CHANGES:
        raise ValueError(f"file.changed kind must be one of {', '.join(FILE_CHANGES)}")
    if event["type"] == "step":
        unknown = set(event) - TraceStep.names() - {"type", "session", "parts"}
        if unknown:
            raise ValueError(f"step event with fields the trace does not have: {', '.join(sorted(unknown))}")
    return event

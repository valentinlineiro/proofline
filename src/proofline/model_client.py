"""Muse Spark via Meta Model API (OpenAI-compatible), using native tool calling.
The model proposes through exactly one tool call: structured edits OR one clarification question. No shell, no verdicts."""
import json
import os
from typing import Callable

from openai import OpenAI
from pydantic import BaseModel, ValidationError, model_validator

from .workspace import Edit

BASE_URL = "https://api.meta.ai/v1"


class ModelError(Exception):
    """API failure, timeout, or a response that is not exactly one valid tool call."""


class EditSet(BaseModel):
    """Either edits or a clarification question, never both and never neither."""
    summary: str = ""
    edits: list[Edit] = []
    clarification: str | None = None

    @model_validator(mode="after")
    def _edits_xor_clarification(self):
        if bool(self.edits) == bool(self.clarification):
            raise ValueError("reply with edits or a clarification, not both or neither")
        return self


TOOLS = [
    {"type": "function", "function": {
        "name": "apply_edits",
        "description": "Replace text in the editable file. Each `old` must occur exactly once in the file.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "One or two sentences: what you changed and any assumption you made."},
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["path", "old", "new"]}}},
            "required": ["summary", "edits"]}}},
    {"type": "function", "function": {
        "name": "ask_clarification",
        "description": "Ask ONE specific question when the task lacks information essential to implement it.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]

SYSTEM = (
    "You are a coding agent changing a Java/Maven repository. Call exactly one tool: `apply_edits` to change code, or "
    "`ask_clarification` if the task lacks information essential to implement it (numbers, thresholds, method signature or "
    "rules are not stated): do not guess. Edit only the files you are shown. You cannot run code and you do not decide "
    "whether the change is correct; an independent verifier does. If given failing check output, fix the code accordingly. "
    "For a minor ambiguity, pick the most reasonable reading and state that assumption in `summary`."
)


def _from_tool_calls(calls: list) -> EditSet:
    if len(calls) != 1:
        raise ModelError("expected exactly one tool call")
    name, raw = calls[0].function.name, calls[0].function.arguments
    try:
        args = json.loads(raw)
        if name == "apply_edits":
            return EditSet.model_validate(args)
        if name == "ask_clarification":
            return EditSet(clarification=args["question"])
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError):
        raise ModelError("tool call arguments did not match the schema") from None
    raise ModelError("unknown tool")


def _usage(resp) -> dict:
    """Token counts exactly as the API reported them: never estimated, never a cost. Anything else is explicitly unavailable."""
    u = getattr(resp, "usage", None)
    got = {k: getattr(u, k, None) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    if all(type(v) is int for v in (got["prompt_tokens"], got["completion_tokens"])):
        return {"available": True, "input_tokens": got["prompt_tokens"], "output_tokens": got["completion_tokens"],
                "total_tokens": got["total_tokens"] if type(got["total_tokens"]) is int else None}
    return {"available": False}


def propose_edits(task: str, files: dict[str, str], feedback: str = "", timeout_s: float = 60,
                  on_usage: Callable[[dict], None] | None = None) -> EditSet:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise ModelError("MODEL_API_KEY is not set")
    # model id is configurable: the exact Muse Spark id comes from the Meta Model API docs
    model = os.environ.get("MODEL_NAME", "muse-spark-1.3")
    shown = "\n\n".join(f"=== {p} ===\n{c}" for p, c in files.items())
    user = f"Task: {task}\n\nEditable files:\n{shown}" + (f"\n\nThe verifier rejected your last attempt:\n{feedback}" if feedback else "")
    client = OpenAI(api_key=key, base_url=BASE_URL, timeout=timeout_s, max_retries=1)
    try:
        resp = client.chat.completions.create(
            model=model, tools=TOOLS, tool_choice="auto",  # the API only supports "auto": a text-only reply must fail closed
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
        calls = list(resp.choices[0].message.tool_calls or [])
    except Exception as e:  # SDK errors can echo request details; keep only the type
        raise ModelError(f"model call failed: {type(e).__name__}") from None
    if on_usage:
        on_usage(_usage(resp))  # reported per call, before the reply is judged valid or not
    if not calls:
        raise ModelError("model did not call a tool")
    return _from_tool_calls(calls)

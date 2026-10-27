"""Muse Spark via Meta Model API (OpenAI-compatible). Proposes structured edits only: no shell, no verdicts."""
import json
import os

from openai import OpenAI
from pydantic import BaseModel, ValidationError, model_validator

from .workspace import Edit

BASE_URL = "https://api.meta.ai/v1"


class ModelError(Exception):
    """API failure, timeout, or a response that does not match the schema."""


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


SYSTEM = (
    "You are a coding agent changing a Java/Maven repository. Reply with ONLY a JSON object: "
    '{"summary": str, "edits": [{"path": str, "old": str, "new": str}]}. '
    "Each edit replaces the text `old`, which must occur exactly once in the file, with `new`. "
    "Edit only the files you are shown. You cannot run code and you do not decide whether the change is correct; "
    "an independent verifier does. If given failing check output, fix the code accordingly. "
    "If the task lacks information essential to implement it (numbers, thresholds, method signature or rules are not "
    'stated), do not guess: reply with ONLY {"clarification": "<one specific question>"}. '
    "For a minor ambiguity, pick the most reasonable reading and state that assumption in `summary`."
)


def propose_edits(task: str, files: dict[str, str], feedback: str = "", timeout_s: float = 60) -> EditSet:
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
            model=model, messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
        text = resp.choices[0].message.content or ""
    except Exception as e:  # SDK errors can echo request details; keep only the type
        raise ModelError(f"model call failed: {type(e).__name__}") from None
    try:
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return EditSet.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError):
        raise ModelError("model response did not match the edit schema") from None

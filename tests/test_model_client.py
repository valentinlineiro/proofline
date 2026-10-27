import json

import pytest
from unittest.mock import patch

from evaluations.harness import fake_client, fake_tool_message
from proofline import model_client
from proofline.model_client import ModelError, propose_edits

EDIT = {"path": "src/main/java/shop/DiscountService.java", "old": "a", "new": "b"}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-123")


def ask(message):
    c = fake_client(message)
    with patch.object(model_client, "OpenAI", return_value=c):
        return propose_edits("t", {"f": "x"}), c


def test_shouldReturnEditsWhenModelCallsApplyEdits():
    r, _ = ask(fake_tool_message("apply_edits", {"summary": "s", "edits": [EDIT]}))
    assert r.edits[0].new == "b" and r.clarification is None


def test_shouldReturnQuestionWhenModelCallsAskClarification():
    r, _ = ask(fake_tool_message("ask_clarification", {"question": "Which percentage?"}))
    assert r.clarification == "Which percentage?" and r.edits == []


def test_shouldOfferBothToolsAndOnlyAutoChoiceBecauseTheApiSupportsNothingElse():
    _, c = ask(fake_tool_message("ask_clarification", {"question": "q"}))
    kw = c.chat.completions.create.call_args.kwargs
    assert {t["function"]["name"] for t in kw["tools"]} == {"apply_edits", "ask_clarification"}
    assert kw["tool_choice"] == "auto"


@pytest.mark.parametrize("message", [
    fake_tool_message(None, text="Here is my plan, no tool call"),          # text only: fail closed
    fake_tool_message("run_shell", {"cmd": "rm -rf /"}),                      # unknown tool
    fake_tool_message("apply_edits", "{not json"),                            # arguments are not JSON
    fake_tool_message("apply_edits", {"summary": "x"}),                       # neither edits nor question
    fake_tool_message("ask_clarification", {"nope": 1}),                      # missing field
    fake_tool_message("apply_edits", {"summary": "s", "edits": [EDIT], "clarification": "q"}),  # both
])
def test_shouldFailClosedWhenReplyIsNotExactlyOneValidToolCall(message):
    c = fake_client(message)
    with patch.object(model_client, "OpenAI", return_value=c), pytest.raises(ModelError):
        propose_edits("t", {"f": "x"})


def test_shouldRejectTwoToolCallsAtOnce():
    m = fake_tool_message("apply_edits", {"summary": "s", "edits": [EDIT]})
    m.tool_calls = m.tool_calls * 2
    c = fake_client(m)
    with patch.object(model_client, "OpenAI", return_value=c), pytest.raises(ModelError):
        propose_edits("t", {"f": "x"})


def test_shouldNotLeakKeyWhenApiCallFails():
    c = fake_client(TimeoutError("boom sk-secret-123"))
    with patch.object(model_client, "OpenAI", return_value=c), pytest.raises(ModelError) as e:
        propose_edits("t", {"f": "x"})
    assert "sk-secret-123" not in str(e.value)

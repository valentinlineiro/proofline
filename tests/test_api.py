import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from proofline import api
from proofline.evidence import EvidenceLog


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EVIDENCE", tmp_path)
    return TestClient(api.app)


def test_shouldServeRecordsAndChainValidityWhenRunExists(client, tmp_path):
    log = EvidenceLog(tmp_path / "auto-1.jsonl")
    log.append("task", task="t", seeded=False)
    log.append("verdict", verdict="PASS", scope=[], fully_verified=False)
    body = client.get("/api/runs/auto-1").json()
    assert body["chain_valid"] is True and [r["kind"] for r in body["records"]] == ["task", "verdict"]
    assert body["running"] is False


def test_shouldReportInvalidChainWhenEvidenceWasTamperedWith(client, tmp_path):
    log = EvidenceLog(tmp_path / "auto-2.jsonl")
    log.append("verdict", verdict="FAIL")
    f = tmp_path / "auto-2.jsonl"
    f.write_text(f.read_text().replace("FAIL", "PASS"))
    assert client.get("/api/runs/auto-2").json()["chain_valid"] is False


@pytest.mark.parametrize("run_id", ["..%2Fsecret", "UPPER", "a" * 60, "x.y"])
def test_shouldReturn404WhenRunIdIsNotAnIdentifier(client, run_id):
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_shouldRejectUnknownSeed(client):
    assert client.post("/api/runs", json={"task": "t", "seed": "../x"}).status_code == 422


def test_shouldAllowOnlyOneRunAtATime(client):
    gate = threading.Event()
    with patch.object(api, "run_new", side_effect=lambda *a: gate.wait(5)):
        first = client.post("/api/runs", json={"task": "t"})
        assert first.status_code == 202
        assert client.post("/api/runs", json={"task": "t"}).status_code == 409
        assert client.get(f"/api/runs/{first.json()['run_id']}").json()["running"] is True
        gate.set()


def test_shouldRecordRunErrorWhenTheRunCrashes(client, tmp_path):
    done = threading.Event()

    def boom(*a):
        raise RuntimeError("x")

    with patch.object(api, "run_new", side_effect=boom):
        run_id = client.post("/api/runs", json={"task": "t"}).json()["run_id"]
        for _ in range(100):
            if not client.get(f"/api/runs/{run_id}").json()["running"]:
                break
            done.wait(0.05)
    assert any(r["kind"] == "run_error" for r in client.get(f"/api/runs/{run_id}").json()["records"])


@pytest.mark.parametrize("task", ["", "   \n"])
def test_shouldRejectEmptyTaskBeforeCallingTheModel(client, task):
    with patch.object(api, "run_new") as run:
        assert client.post("/api/runs", json={"task": task}).status_code == 422
    assert run.call_count == 0

import shutil

import pytest
from fastapi.testclient import TestClient

from proofline import api, review
from proofline.evidence import EvidenceLog
from tests.test_review import SCOPE, Run


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EVIDENCE", tmp_path)
    monkeypatch.setattr(api, "REPO", None)
    return TestClient(api.app)


def verified(path, verdict="PASS", fully=True):
    log = EvidenceLog(path)
    log.append("task", task="t", seeded=False)
    log.append("run_start", run_id="r1", base_revision="b", diff_sha256="d", diff="", change_sha256="c")
    log.append("verdict", verdict=verdict, scope=SCOPE, fully_verified=fully)


def test_shouldAppendDecisionRecord(client, tmp_path):
    verified(tmp_path / "r-1.jsonl")
    res = client.post("/api/runs/r-1/decision", json={"decision": "approve", "reviewer": "ana"})
    assert res.status_code == 200 and res.json()["override"] is False
    body = client.get("/api/runs/r-1").json()
    assert body["chain_valid"] is True and body["records"][-1]["kind"] == "human_decision"
    assert body["records"][-1]["reviewer"] == "ana"


def test_shouldRejectDecisionWithoutReasonOnReject(client, tmp_path):
    verified(tmp_path / "r-2.jsonl")
    res = client.post("/api/runs/r-2/decision", json={"decision": "reject"})
    assert res.status_code == 422 and "reason" in res.json()["detail"]


def test_shouldNotLetTheClientSetOverrideOrVerdict(client, tmp_path):
    verified(tmp_path / "r-3.jsonl", verdict="FAIL", fully=False)
    assert client.post("/api/runs/r-3/decision", json={"decision": "approve", "override": False}).status_code == 422
    assert client.post("/api/runs/r-3/decision", json={"decision": "approve", "verdict": "PASS"}).status_code == 422
    assert client.post("/api/runs/r-3/decision", json={"decision": "approve"}).status_code == 422  # override needs a reason
    res = client.post("/api/runs/r-3/decision", json={"decision": "approve", "reason": "known and accepted"})
    assert res.status_code == 200 and res.json()["override"] is True and res.json()["verdict"] == "FAIL"


def test_shouldReturn404ForUnknownRunOnDecision(client):
    assert client.post("/api/runs/nope/decision", json={"decision": "approve"}).status_code == 404


def test_shouldReturnSignalsAndGateInRun(tmp_path, monkeypatch):
    r = Run(tmp_path)
    runs = r.repo / ".proofline/runs"
    runs.mkdir()
    shutil.copy(r.file, runs / "r-4.jsonl")
    monkeypatch.setattr(api, "EVIDENCE", runs)
    monkeypatch.setattr(api, "REPO", r.repo)
    c = TestClient(api.app)
    assert c.get("/api/runs/r-4").json()["gate"] == review.NOT_APPROVED
    c.post("/api/runs/r-4/decision", json={"decision": "reject", "reason": "no"})
    body = c.get("/api/runs/r-4").json()
    assert body["gate"] == review.REJECTED and body["signals"] == ["REJECTED_PASS"]


def test_shouldHaveNoGateOutsideRepoMode(client, tmp_path):
    verified(tmp_path / "r-5.jsonl")
    body = client.get("/api/runs/r-5").json()
    assert body["gate"] is None and body["signals"] == []


def test_shouldNotStartRunsInRepoMode(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "EVIDENCE", tmp_path)
    monkeypatch.setattr(api, "REPO", tmp_path)
    c = TestClient(api.app)
    assert c.get("/api/mode").json() == {"repo": tmp_path.name}
    assert c.post("/api/runs", json={"task": "t"}).status_code == 409


# ---- fixes from the final review ----
from tests.test_cli import repo, runs, verify  # noqa: E402,F401  (fixtures and helpers: a real run created by the CLI)


def test_shouldOpenAndDecideOnARunCreatedByTheCli(repo, monkeypatch):
    verify(repo)
    run = runs(repo)[0].stem
    monkeypatch.setattr(api, "EVIDENCE", repo / ".proofline/runs")
    monkeypatch.setattr(api, "REPO", repo)
    c = TestClient(api.app)
    assert c.get(f"/api/runs/{run}").status_code == 200
    assert c.post(f"/api/runs/{run}/decision", json={"decision": "approve", "reviewer": "ana"}).status_code == 200
    assert c.get(f"/api/runs/{run}").json()["gate"] == review.OK


def test_shouldRefuseRequestsWithAForeignHostHeader(client):
    assert client.get("/api/mode", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/mode", headers={"host": "localhost:8765"}).status_code == 200

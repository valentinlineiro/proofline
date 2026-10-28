import pytest

from evaluations import harness


@pytest.mark.parametrize("verdict,violated,fault,expected", [
    ("PASS", True, False, "FALSE_PASS"),
    ("PASS", False, False, "TRUE_PASS"),
    ("FAIL", True, False, "DETECTED"),
    ("FAIL", False, False, "FALSE_FAIL"),
    ("INCONCLUSIVE", False, False, "UNEXPECTED_INCONCLUSIVE"),
    ("INCONCLUSIVE", False, True, "CORRECT_INCONCLUSIVE"),
    ("PASS", False, True, "FALSE_PASS"),
    ("FAIL", False, True, "UNEXPECTED_FAIL"),
])
def test_shouldClassifyOutcomeWhenVerdictAndOracleAreKnown(verdict, violated, fault, expected):
    assert harness.classify_outcome(verdict, violated, fault) == expected


@pytest.mark.parametrize("verdict,expected", [("PASS", "UNGROUNDED_PASS"), ("FAIL", "UNGROUNDED_FAIL")])
def test_shouldNeverCountAsFunctionalSuccessWhenRequirementIsUngrounded(verdict, expected):
    assert harness.classify_outcome(verdict, False, False, ungrounded=True) == expected


@pytest.mark.parametrize("model,verdict,outcome", [("sim", "PASS", "TRUE_PASS"), ("sim-bad", "FAIL", "DETECTED")])
def test_shouldDetectKnownRoundingDefectWhenOracleIsIndependentOfTheAgent(tmp_path, monkeypatch, model, verdict, outcome):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(harness, "OUT", tmp_path)
    r = harness.run_one(harness.load("E3"), model, 0)
    assert (r["verdict"], r["outcome"]) == (verdict, outcome)


def test_shouldReportKnownFalsePassWhenDefectIsOutsideMandatoryChecks(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(harness, "OUT", tmp_path)
    r = harness.run_one(harness.load("U1"), "sim", 0)
    # documents current behaviour: the verifier accepts what its mandatory checks cannot see
    assert (r["verdict"], r["oracle_verdict"], r["outcome"]) == ("PASS", "FAIL", "FALSE_PASS")
    assert r["missing_evidence"] == []


def test_shouldReportCorrectInconclusiveWhenMandatoryCheckCannotRun(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(harness, "OUT", tmp_path)
    r = harness.run_one(harness.load("U2"), "sim", 0)
    assert (r["verdict"], r["oracle_verdict"], r["outcome"]) == ("INCONCLUSIVE", "PASS", "CORRECT_INCONCLUSIVE")
    assert r["missing_evidence"] == []


# ---- explicit project selection (retry-policy) -------------------------------------------------------------------------
import json  # noqa: E402

from proofline import model_client  # noqa: E402
from proofline.evidence import verify_chain  # noqa: E402
from proofline.project import ProjectError, load as load_project  # noqa: E402

RP = "retry-policy"


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(harness, "OUT", tmp_path)
    return tmp_path


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(model_client, "OpenAI", lambda **kw: pytest.fail("a model client must not be created before the selection is valid"))


def recs(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_shouldDefaultToTheDiscountProjectWhenATaskOmitsProject():
    assert harness.load("S1").project == "discount-service" and harness.load("RP1").project == RP


def test_shouldKeepRetryTaskTextInSyncWithTheManifest():
    assert all(harness.load(t).task == load_project(RP).default_task for t in ("RP1", "RP2"))


def test_shouldRunRetryPolicyThroughItsOwnManifestAndRecordTheProject(out, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False); monkeypatch.setattr(harness, "OUT", out)
    r = harness.run_one(harness.load("RP1"), "sim", 0)
    assert (r["project"], r["verdict"], r["oracle_verdict"], r["outcome"], r["missing_evidence"]) == (RP, "PASS", "PASS", "TRUE_PASS", [])
    trace = recs(out / "RP1-sim-0.jsonl")
    assert next(x for x in trace if x["kind"] == "task")["project"] == RP
    assert [x["name"] for x in trace if x["kind"] == "check"] == [n for n, _ in load_project(RP).checks]  # exactly its checks
    verdict = [x for x in trace if x["kind"] == "verdict"][-1]
    assert {s["id"] for s in verdict["scope"]} == {"R1", "R2", "R3"} and verdict["fully_verified"]
    assert verify_chain(out / "RP1-sim-0.jsonl") and r["usage"]["input_tokens"] is None  # simulated: no usage reported, none invented


def test_shouldRepairTheSeededDefectInVerifyFirstModeAndWriteOneSummaryPerProject(out, monkeypatch):
    harness.main(["sim", "1", "--project", RP, "RP2"])
    assert (out / "summary-sim-retry-policy.json").exists() and not (out / "summary-sim.json").exists()
    row = json.loads((out / "summary-sim-retry-policy.json").read_text())[0]
    assert (row["task"], row["project"], row["verdict"], row["attempts"], row["outcome"]) == ("RP2", RP, "PASS", 1, "TRUE_PASS")
    trace = recs(out / "RP2-sim-0.jsonl")
    assert trace[0]["seeded"] is True and [x["verdict"] for x in trace if x["kind"] == "verdict"] == ["FAIL", "PASS"]


def test_shouldDetectTheKnownOffByOneWhenAllChecksAreMandatory(out, monkeypatch):
    r = harness.run_one(harness.load("RP1"), "sim-bad", 0)
    assert (r["verdict"], r["oracle_verdict"], r["outcome"]) == ("FAIL", "FAIL", "DETECTED")


def test_shouldKeepTheOracleOnTheFullCheckListWhenTheTaskUsesASubset(out, monkeypatch):
    t = harness.load("RP1").model_copy(update={"id": "RPX", "mandatory_checks": ["maven_tests", "acceptance_backoff_growth"]})
    r = harness.run_one(t, "sim-bad", 0)  # the off-by-one is invisible to the agent's two checks...
    assert (r["verdict"], r["oracle_verdict"], r["outcome"]) == ("PASS", "FAIL", "FALSE_PASS")  # ...but not to the oracle
    names = lambda f: [x["name"] for x in recs(out / f) if x["kind"] == "check"]
    assert names("RPX-sim-bad-0.jsonl") == ["maven_tests", "acceptance_backoff_growth"]
    assert names("RPX-sim-bad-0.oracle.jsonl") == [n for n, _ in load_project(RP).checks]


def test_shouldReportInconclusiveWhenAMandatoryCheckCannotRunInTheSelectedProject(out, monkeypatch):
    t = harness.load("RP1").model_copy(update={"id": "RPF", "fault": "acceptance_backoff_growth"})
    r = harness.run_one(t, "sim", 0)
    assert (r["verdict"], r["outcome"], r["attempts"]) == ("INCONCLUSIVE", "CORRECT_INCONCLUSIVE", 1)  # never a PASS, never repaired


def test_shouldFailClosedBeforeAnyWorkWhenTheProjectIsUnknown(out, no_model):
    t = harness.load("RP1").model_copy(update={"project": "nope"})
    with pytest.raises(ProjectError):
        harness.run_one(t, "sim", 0)
    assert list(out.iterdir()) == []  # no workspace, no evidence, no model client


def test_shouldFailClosedWhenATaskNamesACheckTheProjectDoesNotDefine(out):
    for update in ({"mandatory_checks": ["maven_tests", "nope"]}, {"fault": "nope"}, {"seed": "nope"}):
        with pytest.raises(ProjectError):
            harness.profile(harness.load("RP1").model_copy(update=update))


def test_shouldNotLetATaskWeakenTheOracleWithChecksTheManifestDoesNotDefine(out):
    rp = harness.load("RP1")
    manifest = [list(c) for c in load_project(RP).checks]
    harness.profile(rp.model_copy(update={"checks": manifest}))                              # the manifest's own list: fine
    with pytest.raises(ProjectError, match="not defined"):
        harness.profile(rp.model_copy(update={"checks": manifest + [["extra", "AcceptanceRetryPolicyTest#nothing"]]}))
    with pytest.raises(ProjectError, match="not defined"):                                   # same name, different selector
        harness.profile(rp.model_copy(update={"checks": [["maven_tests", "NoSuchTest"]] + manifest[1:]}))
    harness.profile(harness.load("S1"))                                                      # own oracle (acceptance_dir + checks): allowed


@pytest.mark.parametrize("acceptance_dir", ["/etc", "../outside", "evaluations/oracle/NOPE", "README.md"])
def test_shouldRejectAnOracleDirectoryThatEscapesOrDoesNotExist(out, acceptance_dir):
    with pytest.raises(ProjectError):
        harness.profile(harness.load("S1").model_copy(update={"acceptance_dir": acceptance_dir}))


def test_shouldFailClosedWhenTheManifestIsInvalid(out, monkeypatch, tmp_path):
    projects = tmp_path / "projects"; projects.mkdir()
    (projects / "retry-policy.json").write_text('{"id": "retry-policy", "surprise": 1}')
    monkeypatch.setattr(harness, "load_project", lambda pid: load_project(pid, projects))
    with pytest.raises(ProjectError):
        harness.profile(harness.load("RP1"))


def test_shouldSelectTasksByTheirOwnProjectWithoutReassigningAny():
    ids = lambda ts: sorted(t.id for t in ts)
    assert ids(harness.select([], RP)) == ["RP1", "RP2"]
    assert "RP1" not in ids(harness.select([], "discount-service")) and len(harness.select([], "discount-service")) == 10
    assert ids(harness.select(["RP1", "S1"], RP)) == ["RP1"] and harness.load("S1").project == "discount-service"
    assert len(harness.select([], None)) == 12
    with pytest.raises(ProjectError):  # an invalid id fails even though the filter matches nothing
        harness.select([], "nope")


@pytest.mark.parametrize("argv,message", [
    (["real", "1", "--project", "nope"], "unknown project"),
    (["real", "1", "--project", RP, "S1"], "no tasks selected"),
    (["real", "--project"], "needs an id"),
    (["real", "1", "NOPE_TASK"], "error"),
])
def test_shouldExitBeforeCallingTheModelWhenTheSelectionIsInvalid(out, no_model, argv, message):
    with pytest.raises(SystemExit, match=message):
        harness.main(argv)  # `no_model` makes any model-client creation fail the test
    assert list(out.iterdir()) == []

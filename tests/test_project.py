import json

import pytest

from proofline.agent_loop import run_new
from proofline.project import DEFAULT_ID, Project, ProjectError, all_ids, load

GOOD = {"id": "demo", "fixture": "fx", "acceptance": "acc", "acceptance_dest": "src/test/java/demo",
        "editable": ["src/main/java/A.java"], "checks": [{"name": "unit", "selector": "!Acceptance*"}, {"name": "acc_a", "selector": "AccT#a"}],
        "default_task": "do it"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "fx/src/main/java").mkdir(parents=True)
    (tmp_path / "fx/src/main/java/A.java").write_text("class A {}")
    (tmp_path / "acc").mkdir()
    (tmp_path / "acc/AccT.java").write_text("class AccT { @Test void a() {} }")
    (tmp_path / "projects").mkdir()
    return tmp_path


def put(root, **over):
    (root / "projects/demo.json").write_text(json.dumps({**GOOD, **over}))
    return load("demo", root / "projects", root)


def test_shouldLoadTheDefaultProjectWithExactlyTheFactsTheVerifierUsedBefore():
    p = load()
    assert p.id == DEFAULT_ID == "discount-service" and DEFAULT_ID in all_ids()
    assert p.editable == ("src/main/java/shop/DiscountService.java",)
    assert p.acceptance_dest == "src/test/java/shop"
    assert p.checks == (
        ("maven_tests", "!Acceptance*"),
        ("acceptance_discount_applies", "AcceptanceDiscountTest#shouldApplyTenPercentWhenSubtotalExceedsLimit"),
        ("acceptance_discount_rounding", "AcceptanceDiscountTest#shouldRoundHalfUpToCentsWhenDiscountLeavesFractionOfCent"),
        ("acceptance_discount_boundary", "AcceptanceDiscountTest#shouldNotDiscountWhenSubtotalEqualsLimit"))
    assert p.fixture.name == "discount-service" and p.acceptance.name == "acceptance-tests" and p.seeds.name == "seeds"


def test_shouldLoadAValidManifestFromAnotherRoot(root):
    p = put(root)
    assert isinstance(p, Project) and p.fixture == root / "fx" and p.checks[1] == ("acc_a", "AccT#a")


@pytest.mark.parametrize("over", [
    {"surprise": 1},                                            # unknown key
    {"editable": []},                                           # nothing editable
    {"checks": []},
    {"default_task": ""},
    {"fixture": "/etc"},                                        # absolute
    {"fixture": "../outside"},                                  # traversal
    {"acceptance": "fx/../../x"},
    {"acceptance_dest": "../../etc"},
    {"editable": ["../acc/T.java"]},
    {"editable": ["src/main/java/Missing.java"]},               # not a file in the fixture
    {"editable": ["src/main/java"]},                            # a directory, not a file
    {"editable": ["src/main/java/A.java", "src/main/java/A.java"]},
    {"checks": [{"name": "x", "selector": "a"}, {"name": "x", "selector": "b"}]},   # duplicate names
    {"checks": [{"name": "Bad Name", "selector": "a"}]},
    {"id": "Demo"},                                             # id must match the file name and the id pattern
    {"checks": [{"name": "x", "selector": ""}]},
    {"seeds": "nope"},                                          # not a directory
])
def test_shouldRejectInvalidManifestExplicitly(root, over):
    with pytest.raises(ProjectError):
        put(root, **over)


def test_shouldRejectSymlinkEscapes(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "B.java").write_text("class B {}")
    (root / "fx/src/main/java/Link.java").symlink_to(outside / "B.java")
    with pytest.raises(ProjectError):
        put(root, editable=["src/main/java/Link.java"])
    (root / "esc").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectError):
        put(root, acceptance="esc")


def test_shouldRejectASelectorThatNamesNoExistingTest(root):
    assert put(root).checks[1] == ("acc_a", "AccT#a")
    for sel in ("AccT#missing", "Nope#a"):
        with pytest.raises(ProjectError, match="no test"):
            put(root, checks=[{"name": "acc_a", "selector": sel}])


@pytest.mark.parametrize("where", ["fx", "acc"])
def test_shouldRejectAnySymlinkInTheFixtureOrTheProtectedTests(root, tmp_path_factory, where):
    outside = tmp_path_factory.mktemp("outside")
    (root / where / "evil").symlink_to(outside, target_is_directory=True)  # a directory link: invisible to is_file()
    with pytest.raises(ProjectError, match="symlinks are not allowed"):
        put(root)


def test_shouldRejectSymlinksInTheSeeds(root, tmp_path_factory):
    (root / "seeds").mkdir(); (root / "seeds/evil").symlink_to(tmp_path_factory.mktemp("outside"), target_is_directory=True)
    with pytest.raises(ProjectError, match="symlinks are not allowed"):
        put(root, seeds="seeds")


def test_shouldRejectSymlinksInTheProtectedTests(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "T.java").write_text("class T {}")
    (root / "acc/T.java").symlink_to(outside / "T.java")
    with pytest.raises(ProjectError, match="symlink"):
        put(root)


def test_shouldRejectRequirementsThatReferenceUndefinedChecks(root):
    (root / "acc/requirements.json").write_text(json.dumps([{"id": "R1", "text": "t", "checks": ["typo_check"]}]))
    with pytest.raises(ProjectError, match="typo_check"):
        put(root)


@pytest.mark.parametrize("pid", ["nope", "../discount-service", "Discount", "", "a/b"])
def test_shouldRefuseUnknownOrMalformedProjectIds(pid):
    with pytest.raises(ProjectError):
        load(pid)


def test_shouldFailClosedWhenAnUnknownProjectIsRequested(tmp_path):
    with pytest.raises(ProjectError):
        run_new("t", None, "r", tmp_path / "ev.jsonl", project_id="nope")
    assert not (tmp_path / "ev.jsonl").exists()  # nothing ran, nothing was claimed


def test_shouldMalformedJsonBeAnExplicitError(root):
    (root / "projects/demo.json").write_text("{not json")
    with pytest.raises(ProjectError):
        load("demo", root / "projects", root)


def test_shouldRunTheSameMavenChecksWhetherOrNotTheProjectIsPassedExplicitly(tmp_path):
    """Same executed checks, same verdict and scope: the default path and the explicit-project path are equivalent."""
    import shutil
    from unittest.mock import patch
    from proofline.runner import RunResult
    from proofline.verifier import ROOT, verify
    ok = RunResult(0, "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0", 0.1)
    base = ROOT / "fixtures" / "discount-service"
    out = []
    for n, project in enumerate((None, load())):
        ws = tmp_path / f"ws{n}"
        shutil.copytree(base, ws)
        with patch("proofline.verifier.run_maven", return_value=ok) as maven:  # args[0] of each call is a per-run sandbox dir: compare args[1]
            r = verify(base, ws, tmp_path / f"e{n}.jsonl", "r", project=project)
        out.append(([c.args[1] for c in maven.call_args_list], r["verdict"], r["checks"], r["scope"], r["fully_verified"]))
    assert out[0] == out[1] and out[0][1] == "PASS" and len(out[0][0]) == 4

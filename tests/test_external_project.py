import json
import shutil
from pathlib import Path

import pytest

from proofline.project import ROOT, ProjectError, load, load_external

MANIFEST = {"id": "shop", "acceptance_dest": "src/test/java/shop",
            "editable": ["src/main/java/shop/DiscountService.java"],
            "checks": [{"name": "maven_tests", "selector": "!Acceptance*"},
                       {"name": "acceptance_discount_applies",
                        "selector": "AcceptanceDiscountTest#shouldApplyTenPercentWhenSubtotalExceedsLimit"},
                       {"name": "acceptance_discount_rounding",
                        "selector": "AcceptanceDiscountTest#shouldRoundHalfUpToCentsWhenDiscountLeavesFractionOfCent"},
                       {"name": "acceptance_discount_boundary",
                        "selector": "AcceptanceDiscountTest#shouldNotDiscountWhenSubtotalEqualsLimit"}],
            "default_task": "apply a discount"}


@pytest.fixture
def repo(tmp_path) -> Path:
    shutil.copytree(ROOT / "fixtures/discount-service", tmp_path / "repo")
    r = tmp_path / "repo"
    shutil.copytree(ROOT / "acceptance-tests", r / ".proofline/acceptance")
    (r / ".proofline/project.json").write_text(json.dumps(MANIFEST))
    return r


def test_shouldLoadExternalProjectWhenManifestIsValid(repo):
    p = load_external(repo)
    assert p.id == "shop" and p.fixture == repo.resolve() and p.root == repo.resolve()
    assert p.acceptance == (repo / ".proofline/acceptance").resolve()
    assert p.manifest == (repo / ".proofline/project.json").resolve()
    assert p.editable == ("src/main/java/shop/DiscountService.java",) and p.ecosystem == "java-maven"


@pytest.mark.parametrize("over", [{"acceptance": "../outside"}, {"acceptance": "/etc"}, {"editable": ["../x.java"]}])
def test_shouldRejectManifestPathEscapingRepo(repo, over):
    (repo / ".proofline/project.json").write_text(json.dumps({**MANIFEST, **over}))
    with pytest.raises(ProjectError):
        load_external(repo)


def test_shouldRejectSymlinkInsideExternalRepo(repo, tmp_path):
    (repo / "src/main/java/shop/Link.java").symlink_to(tmp_path)
    with pytest.raises(ProjectError, match="symlink"):
        load_external(repo)


def test_shouldIgnoreSymlinksInsideGitAndTargetDirectories(repo, tmp_path):
    (repo / ".git").mkdir()
    (repo / ".git/ref").symlink_to(tmp_path)
    assert load_external(repo).id == "shop"


def test_shouldRejectUnknownKeysInExternalManifest(repo):
    (repo / ".proofline/project.json").write_text(json.dumps({**MANIFEST, "fixture": "elsewhere"}))
    with pytest.raises(ProjectError):
        load_external(repo)


def test_shouldFailWhenProoflineDirectoryIsMissing(tmp_path):
    with pytest.raises(ProjectError, match="proofline init"):
        load_external(tmp_path)


def test_shouldRejectSelectorThatNamesNoTestInExternalRepo(repo):
    bad = {**MANIFEST, "checks": [*MANIFEST["checks"], {"name": "x", "selector": "AcceptanceDiscountTest#nope"}]}
    (repo / ".proofline/project.json").write_text(json.dumps(bad))
    with pytest.raises(ProjectError, match="no test"):
        load_external(repo)


def test_shouldLoadBundledProjectsWithTheProoflineRepoAsRoot():
    p = load()
    assert p.root == ROOT.resolve() and p.manifest.name == "discount-service.json"

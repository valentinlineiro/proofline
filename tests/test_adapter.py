import json
import shutil
from unittest.mock import patch

import pytest

from proofline.adapters import KNOWN, get_adapter
from proofline.project import PROJECTS, ProjectError, load
from proofline.runner import RunResult
from proofline.verifier import verify


def test_shouldResolveJavaMavenAdapterByDefault():
    assert get_adapter("java-maven").name == "java-maven"
    assert load().ecosystem == "java-maven" and "java-maven" in KNOWN


def test_shouldRejectUnknownAdapterName():
    with pytest.raises(ProjectError, match="ecosystem"):
        get_adapter("cobol")


def test_shouldRejectUnknownEcosystemWhenLoadingManifest(tmp_path):
    shutil.copytree(PROJECTS, tmp_path / "projects")
    f = tmp_path / "projects" / "discount-service.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "ecosystem": "cobol"}))
    with pytest.raises(ProjectError):
        load("discount-service", projects_dir=tmp_path / "projects")


@pytest.mark.parametrize("r,expected", [
    (RunResult(0, "Tests run: 0, Failures: 0", 1), "INCONCLUSIVE"),
    (RunResult(125, "docker: error", 1), "INCONCLUSIVE"),
    (RunResult(1, "Tests run: 1, Failures: 1 <<< FAILURE!", 1), "FAIL"),
    (RunResult(0, "Tests run: 2, Failures: 0", 1), "PASS"),
])
def test_shouldClassifyThroughTheAdapter(r, expected):
    assert get_adapter("java-maven").classify(r) == expected


def test_shouldCallTheContainerRunnerOncePerCheckWithSurefireSelector(tmp_path):
    project = load()
    ws = tmp_path / "ws"
    shutil.copytree(project.fixture, ws)
    ok = RunResult(0, "Tests run: 1, Failures: 0", 0.1)
    with patch("proofline.verifier.run_maven", return_value=ok) as run:
        verify(project.fixture, ws, tmp_path / "ev.jsonl", project=project)
    assert [c.args[1] for c in run.call_args_list] == [
        ["test", f"-Dtest={sel}", "-Dsurefire.failIfNoSpecifiedTests=true"] for _, sel in project.checks]

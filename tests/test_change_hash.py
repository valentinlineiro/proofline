import os
import shutil
from pathlib import Path

from proofline.change import change_hash, inputs_hash


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def pair(tmp_path: Path, files=None):
    files = files or {"src/A.java": "a", "pom.xml": "p"}
    base = tree(tmp_path / "base", files)
    work = tmp_path / "work"
    shutil.copytree(base, work)
    return base, work


def test_shouldGiveSameHashWhenBaseAndWorkAreCopiedToOtherDirectories(tmp_path):
    base, work = pair(tmp_path)
    (work / "src/A.java").write_text("changed")
    other = tmp_path / "elsewhere"
    shutil.copytree(base, other / "b")
    shutil.copytree(work, other / "w")
    assert change_hash(base, work) == change_hash(other / "b", other / "w")


def test_shouldChangeHashWhenContentModeOrDeletionDiffers(tmp_path):
    base, work = pair(tmp_path)
    (work / "src/A.java").write_text("x")
    edited = change_hash(base, work)
    (work / "src/A.java").write_text("y")
    assert change_hash(base, work) != edited
    (work / "src/A.java").write_text("a")
    os.chmod(work / "src/A.java", 0o755)
    assert change_hash(base, work) != change_hash(base, base)
    os.chmod(work / "src/A.java", 0o644)
    (work / "src/A.java").unlink()
    deleted = change_hash(base, work)
    assert deleted not in (edited, change_hash(base, base))


def test_shouldIgnoreTargetGitAndProoflineDirectories(tmp_path):
    base, work = pair(tmp_path)
    clean = change_hash(base, work)
    for d in ("target", ".git", ".proofline"):
        (work / d).mkdir()
        (work / d / "x").write_text("noise")
    assert change_hash(base, work) == clean


def test_shouldHandleSpacesUnicodeAndDotfilesInNames(tmp_path):
    base, work = pair(tmp_path)
    for name in ("src/with space.java", "src/ñandú.java", ".hidden"):
        (work / name).write_text("n")
    h = change_hash(base, work)
    (work / ".hidden").write_text("m")
    assert change_hash(base, work) != h


def test_shouldGiveStableEmptyChangeHash(tmp_path):
    base, work = pair(tmp_path)
    assert change_hash(base, work) == change_hash(base, base) == change_hash(work, work)


def test_shouldChangeInputsHashWhenManifestOrProtectedTestChanges(tmp_path):
    tests = tree(tmp_path / "acc", {"AcceptanceX.java": "t", "requirements.json": "[]"})
    manifest = tmp_path / "project.json"
    manifest.write_text("{}")
    before = inputs_hash(manifest, tests)
    manifest.write_text('{"a":1}')
    after_manifest = inputs_hash(manifest, tests)
    (tests / "AcceptanceX.java").write_text("T")
    assert len({before, after_manifest, inputs_hash(manifest, tests)}) == 3


def test_shouldRecordCanonicalHashesInRunStartWhenVerifying(tmp_path):
    import json
    from unittest.mock import patch

    from proofline.project import PROJECTS, load
    from proofline.runner import RunResult
    from proofline.verifier import verify

    project = load()
    ws = tmp_path / "ws"
    shutil.copytree(project.fixture, ws)
    ok = RunResult(0, "Tests run: 1, Failures: 0, Errors: 0", 0.1)
    ev = tmp_path / "ev.jsonl"
    with patch("proofline.verifier.run_maven", return_value=ok):
        verify(project.fixture, ws, ev, project=project, manifest_path=PROJECTS / "discount-service.json",
               base_ref={"kind": "dir", "path": str(project.fixture)})
    start = next(json.loads(l) for l in ev.read_text().splitlines() if json.loads(l)["kind"] == "run_start")
    assert start["change_sha256"] == change_hash(project.fixture, ws)
    assert start["inputs_sha256"] == inputs_hash(PROJECTS / "discount-service.json", project.acceptance)
    assert start["base_ref"] == {"kind": "dir", "path": str(project.fixture)}
    assert "diff_sha256" in start

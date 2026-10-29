import shutil

import pytest

from proofline.verifier import ROOT, verify
from proofline.workspace import ALLOWED, Edit, EditRejected, apply_edits, changed_files

BASE = ROOT / "fixtures" / "discount-service"
SERVICE = ALLOWED[0]


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    shutil.copytree(BASE, w)
    return w


@pytest.mark.parametrize("path", [
    "/etc/passwd", "../x", "src/main/../../pom.xml", "pom.xml",
    "src/test/java/shop/DiscountServiceTest.java", "src/test/java/shop/AcceptanceDiscountTest.java", "evidence/run.jsonl"])
def test_shouldRejectWhenPathIsOutsideAllowlist(ws, path):
    with pytest.raises(EditRejected):
        apply_edits(ws, [Edit(path=path, old="a", new="b")])


def test_shouldRejectWhenPathIsASymlink(ws):
    (ws / SERVICE).unlink()
    (ws / SERVICE).symlink_to(ws / "pom.xml")
    with pytest.raises(EditRejected):
        apply_edits(ws, [Edit(path=SERVICE, old="x", new="y")])


@pytest.mark.parametrize("old", ["nonexistent", "", "o"])  # missing, empty, ambiguous
def test_shouldRejectWhenOldTextDoesNotMatchExactlyOnce(ws, old):
    with pytest.raises(EditRejected):
        apply_edits(ws, [Edit(path=SERVICE, old=old, new="z")])


def test_shouldWriteNothingWhenAnyEditIsInvalid(ws):
    before = (ws / SERVICE).read_text()
    with pytest.raises(EditRejected):
        apply_edits(ws, [Edit(path=SERVICE, old="return subtotal;", new="return null;"),
                         Edit(path="pom.xml", old="a", new="b")])
    assert (ws / SERVICE).read_text() == before


def test_shouldApplySequentialEditsToSameFile(ws):
    apply_edits(ws, [Edit(path=SERVICE, old="return subtotal;", new="return a;"),
                     Edit(path=SERVICE, old="return a;", new="return b;")])
    assert "return b;" in (ws / SERVICE).read_text()


def test_shouldFailVerdictWhenProtectedFileWasModifiedBehindTheEditTool(ws, tmp_path):
    (ws / "src/test/java/shop/DiscountServiceTest.java").write_text("// weakened")
    assert changed_files(BASE, ws) == {"src/test/java/shop/DiscountServiceTest.java"}
    r = verify(BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "FAIL" and r["checks"][0]["name"] == "allowlist"


def test_shouldRejectWhenParentDirectoryIsASymlinkOutsideWorkspace(ws, tmp_path):
    outside = tmp_path / "outside"
    shutil.copytree(ws / "src/main/java/shop", outside)
    shutil.rmtree(ws / "src/main/java/shop")
    (ws / "src/main/java/shop").symlink_to(outside)
    with pytest.raises(EditRejected):
        apply_edits(ws, [Edit(path=SERVICE, old="return subtotal;", new="return null;")])
    assert "return subtotal;" in (outside / "DiscountService.java").read_text()


def test_shouldDetectAddedAndDeletedFiles(ws, tmp_path):
    (ws / "src/main/java/shop/Backdoor.java").write_text("class Backdoor {}")
    (ws / "src/test/java/shop/DiscountServiceTest.java").unlink()
    assert changed_files(BASE, ws) == {"src/main/java/shop/Backdoor.java", "src/test/java/shop/DiscountServiceTest.java"}
    assert verify(BASE, ws, tmp_path / "ev.jsonl")["verdict"] == "FAIL"


@pytest.mark.parametrize("kind", ["new_dir_link", "new_file_link", "dir_replaced_by_link"])
def test_shouldTreatSymlinksAsChangedFilesAndVerifierShouldFailBeforeRunningMaven(tmp_path, kind):
    """A symlink (to a directory too) is a change outside the allowlist: the verifier must FAIL without running Maven."""
    from unittest.mock import patch
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "x.java").write_text("class X {}")
    ws = tmp_path / "ws"; shutil.copytree(BASE, ws)
    if kind == "new_dir_link":
        (ws / "src/main/java/evil").symlink_to(outside, target_is_directory=True)
    elif kind == "new_file_link":
        (ws / "src/main/java/shop/Evil.java").symlink_to(outside / "x.java")
    else:
        shutil.rmtree(ws / "src/test/java/shop"); (ws / "src/test/java/shop").symlink_to(outside, target_is_directory=True)
    assert changed_files(BASE, ws)
    with patch("proofline.verifier.run_maven") as maven:
        r = verify(BASE, ws, tmp_path / "ev.jsonl")
    assert r["verdict"] == "FAIL" and maven.call_count == 0


def test_shouldStillDetectDeletionsRenamesAndModificationsAndRetargetedSymlinks(tmp_path):
    base, work = tmp_path / "base", tmp_path / "work"
    for d in (base, work):
        d.mkdir(); (d / "same.txt").write_text("s"); (d / "mod.txt").write_text("a"); (d / "gone.txt").write_text("g"); (d / "old.txt").write_text("o")
        (d / "link").symlink_to("same.txt"); (d / "keep").symlink_to("same.txt")
    (work / "mod.txt").write_text("b"); (work / "gone.txt").unlink(); (work / "old.txt").rename(work / "new.txt")
    (work / "link").unlink(); (work / "link").symlink_to("mod.txt")  # retargeted
    assert changed_files(base, work) == {"mod.txt", "gone.txt", "old.txt", "new.txt", "link"}  # `same.txt` and `keep` untouched


def test_shouldNotConfuseARegularFileWhoseTextLooksLikeASymlinkWithTheSymlink(tmp_path):
    base, work = tmp_path / "base", tmp_path / "work"; base.mkdir(); work.mkdir()
    (base / "x").write_text("symlink:foo"); (work / "x").symlink_to("foo")  # same bytes as the old representation
    assert changed_files(base, work) == {"x"}
    (work / "x").unlink(); (work / "x").write_text("L:foo")
    assert changed_files(base, work) == {"x"}


def test_shouldReportANestedTargetDirectoryAsAChange(tmp_path):
    base, work = tmp_path / "b", tmp_path / "w"
    for d in (base, work):
        (d / "src").mkdir(parents=True)
        (d / "src/A.java").write_text("a")
    (work / "src/target").mkdir()
    (work / "src/target/Evil.java").write_text("class Evil {}")
    assert changed_files(base, work) == {"src/target/Evil.java"}

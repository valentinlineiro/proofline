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

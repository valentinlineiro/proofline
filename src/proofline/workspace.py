"""Controlled edits: structured search/replace on an explicit allowlist, validated before anything is written."""
import os
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from .project import load

# Production code the agent may touch (default project; any other project passes its own list). Anything else
# (tests, acceptance, evidence, pom) is rejected.
ALLOWED = load().editable


class EditRejected(Exception):
    pass


class Edit(BaseModel):
    path: str
    old: str
    new: str


def _check_path(root: Path, path: str, allowed: tuple[str, ...]) -> Path:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or str(p) not in allowed:
        raise EditRejected(f"path not editable: {path!r}")
    target = root / str(p)
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise EditRejected(f"not a regular file: {path!r}")
    return target


def apply_edits(root: Path, edits: list[Edit], allowed: tuple[str, ...] = ALLOWED) -> None:
    """All-or-nothing: every edit is validated against in-memory content before any file is written."""
    if not edits:
        raise EditRejected("no edits")
    content: dict[Path, str] = {}
    for e in edits:
        target = _check_path(root, e.path, allowed)
        text = content[target] if target in content else target.read_text()
        if text.count(e.old) != 1 or not e.old:
            raise EditRejected(f"'old' must match exactly once in {e.path}")
        content[target] = text.replace(e.old, e.new, 1)
    for target, text in content.items():
        target.write_text(text)


def changed_files(base: Path, work: Path) -> set[str]:
    def tree(d: Path) -> dict[str, bytes]:
        # a symlink is an entry of its own (its target text): is_file() alone misses links to directories, and
        # copytree would later follow them and pull outside content into the sandbox without any diff
        return {str(p.relative_to(d)): (b"symlink:" + os.readlink(p).encode() if p.is_symlink() else p.read_bytes())
                for p in d.rglob("*") if (p.is_symlink() or p.is_file()) and "target" not in p.relative_to(d).parts}
    a, b = tree(base), tree(work)
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}

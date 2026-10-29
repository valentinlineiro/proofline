"""Reproducible identity of a change. `diff_sha256` (the text of `diff -ruN`) embeds the absolute paths of the two trees, so
it cannot be recomputed later; these hashes depend only on relative paths, contents and modes."""
import os
from pathlib import Path

from .evidence import sha256
from .workspace import IGNORED_DIRS, changed_files

__all__ = ["IGNORED_DIRS", "change_hash", "inputs_hash"]


def _entry(root: Path, rel: str) -> str:
    p = root / rel
    if p.is_symlink():
        return f"L\0{os.readlink(p)}\0-"
    if p.is_file():
        return f"F\0{sha256(p.read_bytes())}\0{p.stat().st_mode & 0o777:o}"
    return "D\0-\0-"  # present in the base, gone in the work tree


def _mode_only(base: Path, work: Path) -> set[str]:
    """Regular files with equal content but a different mode (changed_files compares content only)."""
    return {p.relative_to(base).as_posix() for p in base.rglob("*")
            if p.is_file() and not p.is_symlink() and not set(IGNORED_DIRS) & set(p.relative_to(base).parts)
            and (work / p.relative_to(base)).is_file() and not (work / p.relative_to(base)).is_symlink()
            and (work / p.relative_to(base)).stat().st_mode & 0o777 != p.stat().st_mode & 0o777}


def change_hash(base: Path, work: Path) -> str:
    """Hash of what differs between `base` and `work`: sorted relative paths, each with its kind, content hash and mode."""
    lines = (f"{rel}\0{_entry(work, rel)}\n" for rel in sorted(changed_files(base, work) | _mode_only(base, work)))
    return sha256("".join(lines))


def inputs_hash(manifest: Path, protected_tests: Path) -> str:
    """Hash of the manifest and every protected-test file: what judged the change, not the change itself."""
    parts = [f"manifest\0{sha256(manifest.read_bytes())}\n"]
    parts += [f"{p.relative_to(protected_tests)}\0{sha256(p.read_bytes())}\n"
              for p in sorted(protected_tests.rglob("*")) if p.is_file()]
    return sha256("".join(parts))

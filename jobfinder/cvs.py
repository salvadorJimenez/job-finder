"""CV files: validated uploads stored by content hash. The user-supplied file name is
only kept for display; it never becomes part of a path."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
NAME_MAX = 255
# extension -> (magic bytes the content must start with, download media type)
TYPES = {
    ".pdf": (b"%PDF-", "application/pdf"),
    ".docx": (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}
_STORED_NAME = re.compile(r"[0-9a-f]{64}\.(pdf|docx)")


class CvError(ValueError):
    pass


class CvTooLarge(CvError):
    pass


def clean_name(raw: str | None) -> str:
    """Basename only, no control characters, at most NAME_MAX characters (keeping the extension)."""
    name = re.split(r"[/\\]", raw or "")[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if name in ("", ".", ".."):
        return ""
    if len(name) > NAME_MAX:
        stem, dot, ext = name.rpartition(".")
        name = stem[: NAME_MAX - len(ext) - 1] + dot + ext if dot else name[:NAME_MAX]
    return name


def extension(name: str) -> str | None:
    lowered = name.lower()
    return next((ext for ext in TYPES if lowered.endswith(ext) and len(lowered) > len(ext)), None)


def validate(raw_name: str | None, data: bytes) -> tuple[str, str]:
    """(display name, extension) for an acceptable upload; CvError / CvTooLarge otherwise."""
    name = clean_name(raw_name)
    ext = extension(name)
    if ext is None:
        raise CvError("Only .pdf and .docx files are accepted")
    if len(data) > MAX_BYTES:
        raise CvTooLarge("The file is larger than 10 MB")
    if not data.startswith(TYPES[ext][0]):
        raise CvError(f"The file content is not a valid {ext[1:].upper()}")
    return name, ext


def store(conn: sqlite3.Connection, cvs_dir: Path, raw_name: str | None, data: bytes, now: str) -> int:
    """Save an upload and return its cv_files id. The same content is stored once."""
    name, ext = validate(raw_name, data)
    sha = hashlib.sha256(data).hexdigest()
    existing = conn.execute("SELECT id FROM cv_files WHERE sha256 = ?", (sha,)).fetchone()
    if existing:
        return existing["id"]
    path = cvs_dir / f"{sha}{ext}"
    if not path.exists():
        cvs_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return conn.execute(
        "INSERT INTO cv_files (original_name, sha256, stored_path, uploaded_at) VALUES (?, ?, ?, ?)",
        (name, sha, str(path), now),
    ).lastrowid


def list_cvs(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, original_name FROM cv_files ORDER BY id DESC")]


def locate(conn: sqlite3.Connection, cvs_dir: Path, cv_id: int) -> tuple[Path, str, str] | None:
    """(file, display name, media type) for a stored CV, or None when unknown or missing on disk.

    Only the hash-based file name from the row is used, resolved inside `cvs_dir`."""
    row = conn.execute("SELECT original_name, stored_path FROM cv_files WHERE id = ?", (cv_id,)).fetchone()
    if row is None:
        return None
    filename = Path(row["stored_path"]).name
    if not _STORED_NAME.fullmatch(filename):
        return None
    path = cvs_dir / filename
    if not path.is_file():
        return None
    return path, row["original_name"], TYPES["." + filename.rsplit(".", 1)[1]][1]

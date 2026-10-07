#!/usr/bin/env python3
"""Documents the agents write for the owner: HTML, Markdown, PDF, plain text.

Headless runs cannot write artifacts, so the document skills (skills/document-*)
write a file under `<home>/exports/documents/` and the reply links it as
`[name](doc:<path relative to exports>)`. The Agents tab shows that link as a
collapsed card (the name and an expand button) that opens the file in an overlay.

This module is the only reader of those files. It serves nothing outside
`<home>/exports`: a path must be relative, resolve inside it (symlinks
included), be a known kind, and fit the size cap. No model call.
"""

from __future__ import annotations

from pathlib import Path

import graph

KINDS = {
    ".md": "md", ".markdown": "md",
    ".txt": "txt", ".log": "txt", ".csv": "txt", ".json": "txt",
    ".html": "html", ".htm": "html",
    ".pdf": "pdf",
}
TEXT_KINDS = {"md", "txt", "html"}
MAX_BYTES = 5 * 1024 * 1024
MEDIA = {"md": "text/markdown", "txt": "text/plain", "html": "text/html", "pdf": "application/pdf"}


def root() -> Path:
    return (graph.home_dir() / "exports").resolve()


def resolve(rel: str) -> Path:
    """The file `rel` names, or ValueError saying why it cannot be read."""
    if not rel or rel.startswith(("/", "~")) or "\x00" in rel:
        raise ValueError("a document path is relative to <home>/exports")
    base = root()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ValueError("path leaves <home>/exports")
    if target.suffix.lower() not in KINDS:
        raise ValueError(f"unsupported type: {target.suffix or 'none'}")
    if not target.is_file():
        raise ValueError("no such document")
    if target.stat().st_size > MAX_BYTES:
        raise ValueError("document is over 5 MB")
    return target


def read(rel: str) -> dict:
    """Name, kind and size; the text too for md, txt and html (a PDF is fetched raw)."""
    try:
        target = resolve(rel)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    kind = KINDS[target.suffix.lower()]
    out = {"ok": True, "name": target.name, "path": rel, "kind": kind,
           "size": target.stat().st_size, "text": None}
    if kind in TEXT_KINDS:
        out["text"] = target.read_text(encoding="utf-8", errors="replace")
    return out


def media_type(target: Path) -> str:
    return MEDIA[KINDS[target.suffix.lower()]]

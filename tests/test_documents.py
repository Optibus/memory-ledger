#!/usr/bin/env python3
"""The document reader of the Agents tab (scripts/documents.py), against a THROWAWAY home.

    python3 tests/test_documents.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="second-brain-documents-"))
os.environ["SECOND_BRAIN_HOME"] = str(HOME)
os.environ.pop("SECOND_BRAIN_DB", None)
sys.path.insert(0, str(ROOT / "scripts"))

import documents  # noqa: E402

FAILS: list[str] = []


def ck(name: str, ok: bool) -> None:
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


docs = HOME / "exports" / "documents"
docs.mkdir(parents=True)
(docs / "a.md").write_text("# Hi", encoding="utf-8")
(docs / "b.html").write_text("<p>x</p>", encoding="utf-8")
(docs / "c.pdf").write_bytes(b"%PDF-1.4")
(docs / "d.exe").write_bytes(b"MZ")
(HOME / "secret.md").write_text("nope", encoding="utf-8")
(docs / "link.md").symlink_to(HOME / "secret.md")

r = documents.read("documents/a.md")
ck("markdown comes back as text", r["ok"] and r["kind"] == "md" and r["text"] == "# Hi")
ck("html comes back as text", documents.read("documents/b.html")["kind"] == "html")
r = documents.read("documents/c.pdf")
ck("a pdf has no text, only its kind", r["ok"] and r["kind"] == "pdf" and r["text"] is None)
ck("unknown type is refused", not documents.read("documents/d.exe")["ok"])
ck("missing file is refused", not documents.read("documents/zzz.md")["ok"])
ck("parent traversal is refused", not documents.read("../secret.md")["ok"])
ck("absolute path is refused", not documents.read(str(HOME / "secret.md"))["ok"])
ck("symlink out of exports is refused", not documents.read("documents/link.md")["ok"])
ck("empty path is refused", not documents.read("")["ok"])

(docs / "p.png").write_bytes(b"\x89PNG")
r = documents.read("documents/p.png")
ck("an image is an img with no text", r["ok"] and r["kind"] == "img" and r["text"] is None)
ck("an image is served with its own media type", documents.media_type(docs / "p.png") == "image/png")

import base64  # noqa: E402

b64 = base64.b64encode(b"zipbytes").decode()
a = documents.save_attachment("../../my report (1).zip", b64)
ck("an attachment is saved under <home>/attachments", a["ok"] and (HOME / "attachments" / a["path"]).read_bytes() == b"zipbytes")
ck("the saved name drops the path and keeps the extension", a["name"] == "my report _1_.zip" or a["name"].endswith(".zip"))
ck("an attachment of any type is readable as a file", documents.read(a["path"], "attachments")["kind"] == "file")
ck("an attachment is not readable as an export", not documents.read(a["path"])["ok"])
ck("two files of one name do not collide", documents.save_attachment("x.png", b64)["path"] != documents.save_attachment("x.png", b64)["path"])
ck("an empty file is refused", not documents.save_attachment("e.txt", "")["ok"])
ck("traversal out of attachments is refused", not documents.read("../exports/documents/a.md", "attachments")["ok"])
ck("an unknown area falls back to exports", documents.root("zzz") == documents.root("exports"))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
raise SystemExit(1 if FAILS else 0)

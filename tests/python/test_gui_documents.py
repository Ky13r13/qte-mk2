from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from qte.gui.documents import DocumentLibrary
from qte.gui.safeio import MAX_DOCUMENT_BYTES, SafeReadError, read_registered_file


def _repository(tmp_path: Path, content: bytes) -> Path:
    (tmp_path / "docs" / "strategies").mkdir(parents=True)
    (tmp_path / "docs" / "strategies" / "moving-average-reference.md").write_bytes(content)
    return tmp_path


def test_render_hash_headings_and_unsafe_content(tmp_path):
    source = (b"# Same\n\n# Same\n\n<script>alert(1)</script>\n\n"
              b"[bad](javascript:alert(1)) ![remote](https://attacker.invalid/x.png)\n")
    library = DocumentLibrary(_repository(tmp_path, source))
    result = library.render("moving-average-reference")
    assert result["document"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["document"]["availability"] == "available"
    assert [item["id"] for item in result["headings"]] == [
        "qte-moving-average-reference-h1", "qte-moving-average-reference-h2"]
    html = result["rendered_html"]
    assert "<script>" not in html
    assert "javascript:" not in html
    assert "<img" not in html
    assert "attacker.invalid" not in html


def test_registered_internal_link_and_traversal_are_bounded(tmp_path):
    (tmp_path / "docs" / "strategies").mkdir(parents=True)
    (tmp_path / "docs" / "architecture.md").write_text("# Architecture\n\n## Details here\n")
    (tmp_path / "docs" / "strategies" / "moving-average-reference.md").write_text(
        "# Local title\n\n[local](#local-title) [architecture](../architecture.md) "
        "[details](../architecture.md#details-here) [secret](../../../../etc/passwd)\n")
    html = DocumentLibrary(tmp_path).render("moving-average-reference")["rendered_html"]
    assert '/library/architecture' in html
    assert '/library/architecture#qte-architecture-h2' in html
    assert '#qte-moving-average-reference-h1' in html
    assert "#unavailable" in html
    assert "/etc/passwd" not in html


def test_safe_reader_rejects_symlink_invalid_utf8_and_oversize(tmp_path):
    repo = _repository(tmp_path, b"ok")
    path = repo / "docs" / "strategies" / "moving-average-reference.md"
    path.unlink()
    path.symlink_to(repo / "outside.md")
    (repo / "outside.md").write_text("outside")
    with pytest.raises(SafeReadError) as symlink:
        read_registered_file(repo, "docs/strategies/moving-average-reference.md")
    assert symlink.value.status == 403

    path.unlink()
    path.write_bytes(b"\xff")
    with pytest.raises(SafeReadError) as invalid:
        DocumentLibrary(repo).render("moving-average-reference")
    assert invalid.value.status == 422

    path.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(SafeReadError) as large:
        DocumentLibrary(repo).render("moving-average-reference")
    assert large.value.status == 413


def test_safe_reader_rejects_fifo_without_blocking(tmp_path):
    repo = _repository(tmp_path, b"ok")
    path = repo / "docs" / "strategies" / "moving-average-reference.md"
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(SafeReadError) as fifo:
        read_registered_file(repo, "docs/strategies/moving-average-reference.md")
    assert fifo.value.status == 403


def test_safe_reader_detects_change_during_read(tmp_path, monkeypatch):
    repo = _repository(tmp_path, b"x" * 100)
    path = repo / "docs" / "strategies" / "moving-average-reference.md"
    original_read = os.read
    changed = False

    def changing_read(fd, size):
        nonlocal changed
        result = original_read(fd, size)
        if not changed:
            changed = True
            with path.open("ab") as destination:
                destination.write(b"y")
        return result

    monkeypatch.setattr(os, "read", changing_read)
    with pytest.raises(SafeReadError) as mutation:
        read_registered_file(repo, "docs/strategies/moving-average-reference.md")
    assert mutation.value.status == 409


def test_search_is_literal_case_insensitive_deterministic_and_paged():
    root = Path(__file__).parents[2]
    library = DocumentLibrary(root)
    items, total = library.search("MOVING-AVERAGE", 0, 25)
    assert total == 1
    assert [item["id"] for item in items] == ["moving-average-reference"]
    all_items, total = library.search("", 0, 200)
    assert total >= len(all_items) > 10
    assert all_items == sorted(all_items, key=lambda item: (item["category"].casefold(), item["title"].casefold(), item["id"]))


def test_missing_source_is_explicit(tmp_path):
    library = DocumentLibrary(tmp_path)
    assert not library.available
    with pytest.raises(SafeReadError) as missing:
        library.render("readme")
    assert missing.value.status == 404
    items, total = library.search("QTE", 0, 25)
    assert total == 1
    assert items[0]["availability"] == "unavailable"
    assert items[0]["source_sha256"] is None

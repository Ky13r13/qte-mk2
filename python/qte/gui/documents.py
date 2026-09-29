"""Explicit document registry and safe Markdown presentation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .safeio import SafeReadError, read_registered_file


@dataclass(frozen=True)
class DocumentSpec:
    id: str
    title: str
    category: str
    source_path: str
    implementation_status: str


def _spec(doc_id: str, title: str, category: str, path: str, status: str) -> DocumentSpec:
    return DocumentSpec(doc_id, title, category, path, status)


DOCUMENTS = (
    _spec("gui-usage", "Using the local GUI", "reference", "docs/gui-usage.md", "mixed"),
    _spec("gui-result-bindings", "Read-only result bindings", "reference", "docs/gui-result-bindings.md", "implemented"),
    _spec("readme", "QTE", "overview", "README.md", "mixed"),
    _spec("development-rules", "Development rules", "reference", "AGENTS.md", "implemented"),
    _spec("architecture", "Core architecture", "architecture", "docs/architecture.md", "mixed"),
    _spec("roadmap", "Roadmap", "planning", "docs/roadmap.md", "mixed"),
    _spec("gui-architecture", "GUI architecture", "architecture", "docs/gui-architecture.md", "proposed"),
    _spec("gui-dependencies", "GUI dependencies", "reference", "docs/gui-dependencies.md", "implemented"),
    _spec("gui-g0-preflight", "GUI G0 preflight", "validation", "docs/gui-g0-preflight.md", "implemented"),
    _spec("gui-task-log", "GUI task log", "validation", "docs/gui-task-log.md", "mixed"),
    _spec("research-workflow", "Research workflow", "research", "docs/research-workflow.md", "implemented"),
    _spec("validation-20260922", "Validation record — 2026-09-22", "validation", "docs/validation-20260922.md", "implemented"),
    _spec("moving-average-reference", "Moving-average reference", "strategy", "docs/strategies/moving-average-reference.md", "implemented"),
    _spec("candidate-library", "Candidate library", "strategy", "docs/strategies/candidate-library.md", "mixed"),
    _spec("macro-router", "Macro router", "strategy", "docs/strategies/macro-router.md", "mixed"),
    _spec("regime-framework", "Regime framework", "strategy", "docs/strategies/regime-framework.md", "mixed"),
    _spec("selection-protocol", "Selection protocol", "strategy", "docs/strategies/selection-protocol.md", "proposed"),
    _spec("strategy-lab", "Strategy lab", "strategy", "docs/strategies/strategy-lab.md", "implemented"),
    *tuple(_spec(f"adr-{number:04d}", f"ADR {number:04d}", "adr", f"docs/adr/{number:04d}-{slug}.md", status)
           for number, slug, status in (
               (1, "orders-and-fills", "implemented"), (2, "time-and-events", "implemented"),
               (3, "execution", "implemented"), (4, "portfolio", "implemented"),
               (5, "numerics", "implemented"), (6, "python-boundary", "implemented"),
               (7, "paper-coordination", "mixed"), (8, "equity-sampling", "implemented"),
               (9, "regime-research", "mixed"), (10, "local-research-ui", "proposed"),
           )),
)


class DocumentLibrary:
    def __init__(self, repository: Path) -> None:
        self.repository = Path(repository)
        self.by_id = {item.id: item for item in DOCUMENTS}
        self.by_path = {item.source_path: item for item in DOCUMENTS}

    @property
    def available(self) -> bool:
        try:
            read_registered_file(self.repository, "README.md")
            return True
        except SafeReadError:
            return False

    def _read(self, spec: DocumentSpec) -> tuple[bytes, str]:
        raw = read_registered_file(self.repository, spec.source_path)
        try:
            return raw, raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SafeReadError("document_invalid_encoding", 422, "Document is not valid UTF-8.") from None

    def summary(self, spec: DocumentSpec) -> dict:
        result = {
            "id": spec.id, "title": spec.title, "category": spec.category,
            "source_path": spec.source_path, "implementation_status": spec.implementation_status,
        }
        try:
            raw, _ = self._read(spec)
        except SafeReadError as error:
            result.update(source_sha256=None, availability="unavailable", unavailable_reason=error.code)
        else:
            result.update(source_sha256=sha256(raw).hexdigest(), availability="available")
        return result

    def search(self, query: str, offset: int, limit: int) -> tuple[list[dict], int]:
        needle = query.casefold()
        found = [spec for spec in DOCUMENTS if not needle or needle in spec.title.casefold() or needle in spec.source_path.casefold()]
        found.sort(key=lambda item: (item.category.casefold(), item.title.casefold(), item.id))
        summaries = [self.summary(spec) for spec in found[offset:offset + limit]]
        return summaries, len(found)

    def render(self, doc_id: str) -> dict:
        spec = self.by_id.get(doc_id)
        if spec is None:
            raise SafeReadError("document_not_found", 404, "Document unavailable.")
        raw, text = self._read(spec)
        md = MarkdownIt("commonmark", {"html": False, "linkify": False})
        # markdown-it deliberately renders forbidden-scheme Markdown links as
        # literal source. Keep the label but remove the dangerous URL entirely.
        text = re.sub(r"(?is)\[([^\]]*)\]\(\s*(?:javascript|data)\s*:[^\r\n]*?\)",
                      r"[\1](#unavailable)", text)
        tokens = md.parse(text)
        headings: list[dict] = []
        heading_ordinal = 0
        current_heading: tuple[str, int] | None = None
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                heading_ordinal += 1
                level = int(token.tag[1:])
                anchor = f"qte-{spec.id}-h{heading_ordinal}"
                token.attrSet("id", anchor)
                current_heading = (anchor, level)
            elif token.type == "inline":
                if current_heading:
                    headings.append({"id": current_heading[0], "text": token.content, "level": current_heading[1]})
                    current_heading = None
                self._sanitize_inline(token, spec)
        rendered = md.renderer.render(tokens, md.options, {})
        return {
            "schema_version": 1,
            "document": {
                "id": spec.id, "title": spec.title, "category": spec.category,
                "source_path": spec.source_path, "source_sha256": sha256(raw).hexdigest(),
                "implementation_status": spec.implementation_status,
                "availability": "available",
            },
            "rendered_html": rendered,
            "headings": headings,
        }

    def _sanitize_inline(self, token, source: DocumentSpec) -> None:
        for child in token.children or ():
            if child.type == "image":
                child.type = "text"
                child.tag = ""
                child.content = f"[image unavailable: {child.content}]"
                child.attrs = {}
            elif child.type == "link_open":
                href = child.attrGet("href") or ""
                target = self._internal_target(source, href)
                if target is None:
                    child.attrSet("href", "#unavailable")
                    child.attrSet("aria-disabled", "true")
                else:
                    child.attrSet("href", target)

    def _internal_target(self, source: DocumentSpec, href: str) -> str | None:
        try:
            split = urlsplit(href)
        except ValueError:
            return None
        if split.scheme or split.netloc:
            return None
        decoded = unquote(split.path)
        if not decoded:
            if not split.fragment:
                return "#"
            ordinal = self._heading_ordinal(source, unquote(split.fragment))
            return f"#qte-{source.id}-h{ordinal}" if ordinal is not None else None
        candidate = PurePosixPath(source.source_path).parent.joinpath(decoded)
        normalized: list[str] = []
        for part in candidate.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not normalized:
                    return None
                normalized.pop()
            else:
                normalized.append(part)
        target = self.by_path.get("/".join(normalized))
        if target is None:
            return None
        suffix = ""
        if split.fragment:
            ordinal = self._heading_ordinal(target, unquote(split.fragment))
            if ordinal is None:
                return None
            suffix = f"#qte-{target.id}-h{ordinal}"
        return f"/library/{target.id}{suffix}"

    def _heading_ordinal(self, target: DocumentSpec, fragment: str) -> int | None:
        try:
            _, text = self._read(target)
        except SafeReadError:
            return None
        tokens = MarkdownIt("commonmark", {"html": False, "linkify": False}).parse(text)
        wanted = self._heading_key(fragment)
        ordinal = 0
        for index, token in enumerate(tokens):
            if token.type != "heading_open":
                continue
            ordinal += 1
            if index + 1 < len(tokens) and self._heading_key(tokens[index + 1].content) == wanted:
                return ordinal
        return None

    @staticmethod
    def _heading_key(value: str) -> str:
        value = value.casefold().strip().replace(" ", "-")
        return re.sub(r"[^\w-]", "", value)

"""JATS XML -> document model -> extractor input (markdown with stable section anchors).

The extractor cites evidence by anchor (e.g. "sec:Case presentation ¶3", "tab:1 r2c4", "abstract"),
so anchors must be deterministic. Tables are rendered as pipe grids with row/column indices because
case series put per-patient data in tables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

_WS = re.compile(r"\s+")


def _t(el: etree._Element | None) -> str:
    if el is None:
        return ""
    return _WS.sub(" ", "".join(el.itertext())).strip()


@dataclass
class Table:
    label: str
    caption: str
    rows: list[list[str]]
    footnotes: str = ""

    def to_markdown(self, idx: int) -> str:
        out = [f"### Table {self.label or idx} [tab:{idx}]", self.caption]
        if self.rows:
            width = max(len(r) for r in self.rows)
            for ri, row in enumerate(self.rows, 1):
                cells = [c.replace("|", "/") for c in row] + [""] * (width - len(row))
                out.append(f"| r{ri} | " + " | ".join(cells) + " |")
                if ri == 1:
                    out.append("|" + "---|" * (width + 1))
        if self.footnotes:
            out.append(f"Footnotes: {self.footnotes}")
        return "\n".join(out)


@dataclass
class Section:
    title: str
    paragraphs: list[str]
    depth: int = 1


@dataclass
class Document:
    title: str = ""
    abstract: str = ""
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    figures: list[tuple[str, str]] = field(default_factory=list)  # (label, caption)
    supplements: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self, max_chars: int | None = None, include_references: bool = False) -> str:
        parts = [f"# {self.title}"]
        if self.meta:
            parts.append("Metadata: " + "; ".join(f"{k}={v}" for k, v in self.meta.items() if v))
        if self.abstract:
            parts.append("## Abstract [abstract]\n" + self.abstract)
        for si, s in enumerate(self.sections, 1):
            if not include_references and re.match(r"^(references|bibliography|acknowledg|competing interests|funding|author contributions)", s.title, re.I):
                continue
            hdr = "#" * min(2 + s.depth - 1, 4)
            parts.append(f"{hdr} {s.title} [sec:{si}]")
            for pi, p in enumerate(s.paragraphs, 1):
                parts.append(f"[sec:{si} ¶{pi}] {p}")
        for ti, t in enumerate(self.tables, 1):
            parts.append(t.to_markdown(ti))
        for label, cap in self.figures:
            parts.append(f"Figure {label}: {cap}")
        if self.supplements:
            parts.append("Supplementary material: " + "; ".join(self.supplements))
        text = "\n\n".join(p for p in parts if p)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n\n[TRUNCATED]"
        return text


def _table_rows(tbl: etree._Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in tbl.iter("{*}tr"):
        cells = []
        for c in tr:
            if etree.QName(c).localname in ("td", "th"):
                txt = _t(c)
                span = int(c.get("colspan", "1") or 1)
                cells.append(txt)
                cells.extend([""] * (span - 1))
        if cells:
            rows.append(cells)
    return rows


def parse_jats(xml_path: Path) -> Document:
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.parse(str(xml_path), parser).getroot()
    ns = {"x": root.nsmap.get(None)} if root.nsmap.get(None) else {}

    def find(el: etree._Element, path: str) -> etree._Element | None:
        return el.find(path, ns) if ns else el.find(path)

    def findall(el: etree._Element, path: str) -> list[etree._Element]:
        return el.findall(path, ns) if ns else el.findall(path)

    doc = Document()
    front = find(root, ".//{*}front")
    if front is not None:
        doc.title = _t(find(front, ".//{*}article-title"))
        abs_el = find(front, ".//{*}abstract")
        if abs_el is not None:
            parts = []
            for sec in findall(abs_el, ".//{*}sec"):
                parts.append(f"{_t(find(sec, '{*}title'))}: " + " ".join(_t(p) for p in findall(sec, "{*}p")))
            if not parts:
                parts = [" ".join(_t(p) for p in findall(abs_el, ".//{*}p"))]
            doc.abstract = "\n".join(p for p in parts if p.strip(": "))
        ids = {e.get("pub-id-type"): _t(e) for e in findall(front, ".//{*}article-id")}
        doc.meta = {"pmid": ids.get("pmid"), "pmcid": ids.get("pmcid") or ids.get("pmc"), "doi": ids.get("doi"),
                    "journal": _t(find(front, ".//{*}journal-title")),
                    "year": _t(find(front, ".//{*}pub-date/{*}year")),
                    "license": _t(find(front, ".//{*}license/{*}license-p"))[:120] or (find(front, ".//{*}license").get("{http://www.w3.org/1999/xlink}href") if find(front, ".//{*}license") is not None else None)}

    body = find(root, ".//{*}body")
    if body is not None:
        def walk(sec: etree._Element, depth: int) -> None:
            title = _t(find(sec, "{*}title")) or "(untitled)"
            paras = [_t(p) for p in findall(sec, "{*}p")]
            # paragraphs directly inside boxed-text / list items also count
            doc.sections.append(Section(title=title, paragraphs=[p for p in paras if p], depth=depth))
            for sub in findall(sec, "{*}sec"):
                walk(sub, depth + 1)

        top_secs = findall(body, "{*}sec")
        if top_secs:
            loose = [_t(p) for p in findall(body, "{*}p")]
            if loose:
                doc.sections.append(Section(title="Body", paragraphs=loose, depth=1))
            for s in top_secs:
                walk(s, 1)
        else:
            doc.sections.append(Section(title="Body", paragraphs=[_t(p) for p in body.iter("{*}p")], depth=1))

    # tables and figures anywhere (body, back, floats-group)
    for tw in root.iter("{*}table-wrap"):
        label = _t(find(tw, "{*}label"))
        caption = _t(find(tw, "{*}caption"))
        tbl = find(tw, ".//{*}table")
        rows = _table_rows(tbl) if tbl is not None else []
        foot = _t(find(tw, "{*}table-wrap-foot"))
        doc.tables.append(Table(label=label, caption=caption, rows=rows, footnotes=foot))
    for fig in root.iter("{*}fig"):
        doc.figures.append((_t(find(fig, "{*}label")), _t(find(fig, "{*}caption"))))
    for sm in root.iter("{*}supplementary-material"):
        doc.supplements.append(_t(sm)[:200] or sm.get("{http://www.w3.org/1999/xlink}href", ""))
    return doc


def pdf_to_document(pdf_path: Path) -> Document:
    """Fallback for PDF-only tiers: plain text by page, no structure. GROBID gives better results when available."""
    try:
        import fitz  # pymupdf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install pymupdf for PDF fallback") from e
    doc = Document(title=pdf_path.stem)
    with fitz.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf, 1):
            text = page.get_text("text")
            doc.sections.append(Section(title=f"Page {i}", paragraphs=[p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]))
    return doc


def document_from_metadata(title: str, abstract: str | None, meta: dict[str, Any]) -> Document:
    """Abstract-only tier."""
    return Document(title=title or "", abstract=abstract or "", meta=meta)


def load_document(raw_dir: Path, pmid: str, fallback_title: str = "", fallback_abstract: str | None = None,
                  meta: dict[str, Any] | None = None) -> tuple[Document, str]:
    """Best available representation of a document and the tier it came from."""
    d = raw_dir / pmid
    for name, tier in (("epmc.xml", "epmc"), ("elsevier.xml", "publisher"), ("springer.xml", "publisher"), ("unpaywall.xml", "unpaywall")):
        p = d / name
        if p.exists():
            doc = parse_jats(p)
            if not doc.title:
                doc.title = fallback_title
            if not doc.abstract and fallback_abstract:
                doc.abstract = fallback_abstract
            doc.meta = {**(meta or {}), **{k: v for k, v in doc.meta.items() if v}}
            return doc, tier
    for name, tier in (("unpaywall.pdf", "unpaywall"), ("wiley.pdf", "publisher")):
        p = d / name
        if p.exists():
            doc = pdf_to_document(p)
            doc.title, doc.abstract, doc.meta = fallback_title or doc.title, fallback_abstract or "", meta or {}
            return doc, tier
    return document_from_metadata(fallback_title, fallback_abstract, meta or {}), "abstract"

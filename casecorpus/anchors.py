"""Anchor index over the rendered document (work/<pmid>/input.md).

The renderer (jats.Document.to_markdown) emits deterministic anchors:
  [abstract]                 the abstract block
  ## <title> [sec:N]         a section; its paragraphs are lines starting with "[sec:N ¶M] "
  ### Table <label> [tab:N]  a table; rows are lines "| rI | c1 | c2 | ... |"
  Figure <label>: caption

This module resolves anchors to verbatim text so that (a) the full patient description can be
assembled from the source by anchor — verbatim by construction — and (b) every evidence quote can
be verified against the text of the anchor it claims.

Supported anchor forms: "abstract", "sec:N", "sec:N ¶M", "tab:N", "tab:N rI", "tab:N cJ",
"tab:N rIcJ", "fig:<label>", "title".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SEC_HDR = re.compile(r"^#{2,4} (.+?) \[sec:(\d+)\]\s*$")
_PARA = re.compile(r"^\[sec:(\d+) ¶(\d+)\] (.*)$")
_TAB_HDR = re.compile(r"^### Table (.*?) \[tab:(\d+)\]\s*$")
_ROW = re.compile(r"^\| r(\d+) \| (.*) \|\s*$")
_FIG = re.compile(r"^Figure (\S+): (.*)$")
_ANCHOR = re.compile(r"^(abstract|title|sec:(\d+)(?:\s*¶\s*(\d+))?|tab:(\d+)(?:\s*r(\d+))?(?:\s*c(\d+))?|fig:(.+))$", re.I)


@dataclass
class DocIndex:
    title: str = ""
    abstract: str = ""
    sections: dict[int, dict[str, Any]] = field(default_factory=dict)   # n -> {title, paragraphs: {m: text}}
    tables: dict[int, dict[str, Any]] = field(default_factory=dict)     # n -> {label, caption, rows: {i: [cells]}}
    figures: dict[str, str] = field(default_factory=dict)

    # ---- resolution ------------------------------------------------------
    def resolve(self, anchor: str) -> str | None:
        """Verbatim text for an anchor, or None if it does not exist."""
        a = anchor.strip()
        m = _ANCHOR.match(a)
        if not m:
            return None
        if a.lower() == "abstract":
            return self.abstract or None
        if a.lower() == "title":
            return self.title or None
        if m.group(2):
            sec = self.sections.get(int(m.group(2)))
            if not sec:
                return None
            if m.group(3):
                return sec["paragraphs"].get(int(m.group(3)))
            return "\n\n".join(sec["paragraphs"][k] for k in sorted(sec["paragraphs"])) or None
        if m.group(4):
            tab = self.tables.get(int(m.group(4)))
            if not tab:
                return None
            r, c = m.group(5), m.group(6)
            if r and c:
                row = tab["rows"].get(int(r))
                if not row or int(c) > len(row):
                    return None
                return row[int(c) - 1]
            if r:
                row = tab["rows"].get(int(r))
                return " | ".join(row) if row else None
            if c:
                j = int(c)
                lines = []
                for i in sorted(tab["rows"]):
                    row = tab["rows"][i]
                    if j <= len(row):
                        lines.append(f"{row[0]}: {row[j-1]}" if j > 1 and row else row[j - 1])
                return "\n".join(lines) or None
            head = f"Table {tab['label']}: {tab['caption']}".strip(": ")
            body = "\n".join(" | ".join(tab["rows"][i]) for i in sorted(tab["rows"]))
            return f"{head}\n{body}".strip()
        if m.group(7):
            return self.figures.get(m.group(7).strip())
        return None

    def exists(self, anchor: str) -> bool:
        return self.resolve(anchor) is not None

    def expand(self, anchor: str) -> list[str]:
        """Expand a coarse anchor into its finest units (paragraphs / rows) for narrative assembly."""
        a = anchor.strip()
        m = _ANCHOR.match(a)
        if not m:
            return []
        if m.group(2) and not m.group(3):
            n = int(m.group(2))
            return [f"sec:{n} ¶{k}" for k in sorted(self.sections.get(n, {}).get("paragraphs", {}))]
        if m.group(4) and not m.group(5) and not m.group(6):
            n = int(m.group(4))
            return [f"tab:{n} r{i}" for i in sorted(self.tables.get(n, {}).get("rows", {}))]
        return [a] if self.exists(a) else []


def index_markdown(md: str) -> DocIndex:
    idx = DocIndex()
    lines = md.split("\n")
    mode: str | None = None
    cur_tab: int | None = None
    abstract_lines: list[str] = []
    for line in lines:
        if line.startswith("# ") and not idx.title:
            idx.title = line[2:].strip()
            continue
        if line.startswith("## Abstract [abstract]"):
            mode = "abstract"
            continue
        mh = _SEC_HDR.match(line)
        if mh:
            mode = "section"
            n = int(mh.group(2))
            idx.sections.setdefault(n, {"title": mh.group(1), "paragraphs": {}})
            continue
        mt = _TAB_HDR.match(line)
        if mt:
            mode = "table"
            cur_tab = int(mt.group(2))
            idx.tables[cur_tab] = {"label": mt.group(1), "caption": "", "rows": {}}
            continue
        mp = _PARA.match(line)
        if mp:
            n, m_, text = int(mp.group(1)), int(mp.group(2)), mp.group(3)
            idx.sections.setdefault(n, {"title": "", "paragraphs": {}})["paragraphs"][m_] = text
            continue
        mf = _FIG.match(line)
        if mf:
            idx.figures[mf.group(1)] = mf.group(2)
            mode = None
            continue
        if mode == "abstract":
            if line.strip():
                abstract_lines.append(line.strip())
            elif abstract_lines:
                mode = None
            continue
        if mode == "table" and cur_tab is not None:
            mr = _ROW.match(line)
            if mr:
                cells = [c.strip() for c in mr.group(2).split(" | ")]
                idx.tables[cur_tab]["rows"][int(mr.group(1))] = cells
            elif line.startswith("|---"):
                pass
            elif line.strip() and not idx.tables[cur_tab]["caption"] and not line.startswith("|"):
                idx.tables[cur_tab]["caption"] = line.strip()
            elif line.startswith("Footnotes:"):
                idx.tables[cur_tab]["footnotes"] = line[len("Footnotes:"):].strip()
            continue
    idx.abstract = "\n".join(abstract_lines)
    return idx


_WS = re.compile(r"\s+")


def _squash(s: str) -> str:
    return _WS.sub(" ", s.replace(" ", " ")).strip()


def verify_quote(quote: str, anchor: str, idx: DocIndex, full_text: str) -> dict[str, Any]:
    """Where does this quote occur? in the anchored unit (strict), elsewhere in the document (loose), or nowhere."""
    q = _squash(quote)
    out: dict[str, Any] = {"anchor_exists": idx.exists(anchor), "in_anchor": False, "in_document": False, "offset": None}
    if not q:
        return out
    target = idx.resolve(anchor)
    if target:
        t = _squash(target)
        pos = t.find(q)
        if pos < 0 and len(q) > 80:  # tolerate a truncated tail
            pos = t.find(q[:80])
        if pos >= 0:
            out["in_anchor"] = True
            out["in_document"] = True
            out["offset"] = [pos, pos + len(q)]
            return out
    ft = _squash(full_text)
    if q in ft or (len(q) > 80 and q[:80] in ft):
        out["in_document"] = True
    return out


def assemble_narrative(anchors: list[str], idx: DocIndex) -> list[dict[str, Any]]:
    """Verbatim passages for a list of anchors, expanded to paragraph/row units, deduplicated,
    kept in document order (abstract, then sections by number, then tables)."""
    units: list[str] = []
    seen: set[str] = set()
    for a in anchors:
        for u in idx.expand(a):
            if u not in seen:
                seen.add(u)
                units.append(u)

    def order(u: str) -> tuple:
        if u == "abstract":
            return (0, 0, 0, 0)
        if u == "title":
            return (-1, 0, 0, 0)
        m = _ANCHOR.match(u)
        if m and m.group(2):
            return (1, int(m.group(2)), int(m.group(3) or 0), 0)
        if m and m.group(4):
            return (2, int(m.group(4)), int(m.group(5) or 0), int(m.group(6) or 0))
        return (3, 0, 0, 0)

    out = []
    for u in sorted(units, key=order):
        text = idx.resolve(u)
        if text:
            out.append({"anchor": u, "text": text, "chars": len(text)})
    return out

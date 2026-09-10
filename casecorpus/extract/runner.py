"""Extraction runners.

Two interchangeable engines:

FileExchangeEngine  writes work/<pmid>/input.md and the prompts; expects manifest.json and
                    record_<slug>.json to be written back by whoever performs the extraction
                    (a person, Claude in a Cowork session, or a separate batch job). Nothing is
                    lost if the extractor is slow or offline.
ClaudeEngine        calls the Anthropic API with the JSON schema as a tool definition, so the
                    answer is structured. Supports the Message Batches API for scale.

Both produce the same files under work/<pmid>/ so the ingest step (ground + validate + store)
is identical.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..config import Settings
from ..db import Catalogue
from ..jats import load_document
from . import prompts

MAX_INPUT_CHARS = 180_000  # ~45k tokens; case reports are far smaller, series can be large


def slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:40] or "individual"


@dataclass
class WorkItem:
    pmid: str
    dir: Path
    input_md: Path
    manifest: Path

    def record_path(self, local_id: str) -> Path:
        return self.dir / f"record_{slug(local_id)}.json"


def prepare(settings: Settings, cat: Catalogue, pmids: Iterable[str], scope_description: str) -> list[WorkItem]:
    """Render each document to markdown and write the prompts into work/<pmid>/."""
    settings.ensure()
    items = []
    for pmid in pmids:
        d = cat.get_document(pmid)
        if not d:
            continue
        doc, tier = load_document(settings.raw_dir, pmid, d.get("title") or "", d.get("abstract"),
                                  {"pmid": pmid, "pmcid": d.get("pmcid"), "doi": d.get("doi"), "journal": d.get("journal"),
                                   "year": d.get("pub_year"), "license": d.get("license"), "tier": tier if False else None})
        doc.meta["tier"] = tier
        md = doc.to_markdown(max_chars=MAX_INPUT_CHARS)
        wd = settings.work_dir / pmid
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "input.md").write_text(md)
        (wd / "prompt_triage.txt").write_text(prompts.triage_prompt(scope_description))
        (wd / "prompt_extract_template.txt").write_text(prompts.EXTRACT_SYSTEM.replace("{record_schema}", "<record.schema.json>"))
        (wd / "meta.json").write_text(json.dumps({"pmid": pmid, "tier": tier, "chars": len(md), "prompt_version": prompts.PROMPT_VERSION,
                                                  "title": d.get("title"), "doi": d.get("doi"), "pmcid": d.get("pmcid"),
                                                  "license": d.get("license"), "scope_tags": d.get("scope_tags")}, indent=1))
        items.append(WorkItem(pmid=pmid, dir=wd, input_md=wd / "input.md", manifest=wd / "manifest.json"))
    return items


class ClaudeEngine:
    """Anthropic API engine. Uses tool-use to force schema-shaped JSON."""

    def __init__(self, settings: Settings, model: str = "claude-sonnet-4-5", max_tokens: int = 16000):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install 'casecorpus[llm]'") from e
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model
        self.max_tokens = max_tokens

    def _call(self, system: str, user: str, tool_name: str, schema: dict) -> dict:
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            tools=[{"name": tool_name, "description": "Return the structured result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError("no tool_use block in response")

    def triage(self, item: WorkItem, scope_description: str) -> dict:
        md = item.input_md.read_text()
        out = self._call(prompts.triage_prompt(scope_description), prompts.triage_user(md), "manifest", prompts.MANIFEST_SCHEMA)
        out["_model"], out["_prompt_version"] = self.model, prompts.PROMPT_VERSION
        item.manifest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
        return out

    def extract(self, item: WorkItem, local_id: str, where: list[str] | None = None) -> dict:
        md = item.input_md.read_text()
        out = self._call(prompts.extract_prompt(local_id), prompts.extract_user(md, local_id, where), "record", prompts.RECORD_SCHEMA)
        out["_model"], out["_prompt_version"] = self.model, prompts.PROMPT_VERSION
        item.record_path(local_id).write_text(json.dumps(out, indent=1, ensure_ascii=False))
        return out


def run_engine(settings: Settings, cat: Catalogue, items: list[WorkItem], engine: ClaudeEngine, scope_description: str,
               skip_existing: bool = True) -> dict[str, Any]:
    stats = {"triaged": 0, "in_scope": 0, "records": 0, "errors": 0}
    for item in items:
        try:
            if skip_existing and item.manifest.exists():
                manifest = json.load(open(item.manifest))
            else:
                manifest = engine.triage(item, scope_description)
                stats["triaged"] += 1
            if not manifest.get("in_scope"):
                continue
            stats["in_scope"] += 1
            for ind in manifest.get("individuals", []):
                if not ind.get("is_affected", True):
                    continue
                p = item.record_path(ind["local_id"])
                if skip_existing and p.exists():
                    continue
                engine.extract(item, ind["local_id"], ind.get("where"))
                stats["records"] += 1
                time.sleep(0.2)
        except Exception as e:  # keep going; the file exchange makes retries cheap
            stats["errors"] += 1
            (item.dir / "error.txt").write_text(f"{type(e).__name__}: {e}")
    return stats


def pending(settings: Settings) -> dict[str, list[str]]:
    """What still needs doing in the work directory (for the file-exchange engine)."""
    need_triage, need_extract, done = [], [], []
    for wd in sorted(settings.work_dir.iterdir()):
        if not (wd / "input.md").exists():
            continue
        m = wd / "manifest.json"
        if not m.exists():
            need_triage.append(wd.name)
            continue
        manifest = json.load(open(m))
        if not manifest.get("in_scope"):
            done.append(wd.name)
            continue
        missing = [i["local_id"] for i in manifest.get("individuals", []) if i.get("is_affected", True)
                   and not (wd / f"record_{slug(i['local_id'])}.json").exists()]
        (need_extract if missing else done).append(wd.name)
    return {"need_triage": need_triage, "need_extract": need_extract, "done": done}

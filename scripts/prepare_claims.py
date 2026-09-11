"""Prepare reaction-claim work items: work/<pmid>/input.md + prompt_reaction.txt for every document with full text.
Usage: CASECORPUS_HOME=... python prepare_claims.py [pmid ...]"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from casecorpus.config import Settings  # noqa: E402
from casecorpus.db import Catalogue  # noqa: E402
from casecorpus.jats import load_document  # noqa: E402
from casecorpus.extract.prompts_reaction import REACTION_PROMPT_VERSION, reaction_prompt  # noqa: E402

s = Settings().ensure(); cat = Catalogue(s.db_path)
pmids = sys.argv[1:] or [d["pmid"] for d in cat.iter_documents("fulltext_tier='epmc'")]
for pmid in pmids:
    d = cat.get_document(pmid)
    doc, tier = load_document(s.raw_dir, pmid, d.get("title") or "", d.get("abstract"), {"pmid": pmid, "pmcid": d.get("pmcid"), "doi": d.get("doi"), "journal": d.get("journal"), "year": d.get("pub_year")})
    doc.meta["tier"] = tier
    wd = s.work_dir / pmid; wd.mkdir(parents=True, exist_ok=True)
    md = doc.to_markdown(max_chars=180_000)
    (wd / "input.md").write_text(md)
    (wd / "prompt_reaction.txt").write_text(reaction_prompt())
    (wd / "meta.json").write_text(json.dumps({"pmid": pmid, "tier": tier, "chars": len(md), "prompt_version": REACTION_PROMPT_VERSION, "title": d.get("title"), "task": "reaction_claims"}, indent=1))
    print(pmid, tier, len(md), "chars")

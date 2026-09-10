"""SQLite catalogue.

Tables
------
documents         one row per PubMed record in the corpus (metadata + corpus layer + scope tags)
retrieval         one row per (pmid, tier) attempt, success or not, with the file it produced
triage            triage decision per document (in scope? how many individuals? reasons)
individuals       one row per extracted individual (record JSON + validation status)
links             same_individual_as / family links across papers
runs              provenance of every batch run (command, version, timestamps)
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  pmid            TEXT PRIMARY KEY,
  pmcid           TEXT,
  doi             TEXT,
  title           TEXT,
  abstract        TEXT,
  journal         TEXT,
  journal_iso     TEXT,
  issn            TEXT,
  pub_year        INTEGER,
  pub_date        TEXT,
  language        TEXT,
  pub_types       TEXT,   -- JSON list
  mesh            TEXT,   -- JSON list of {descriptor, major, qualifiers}
  keywords        TEXT,   -- JSON list
  authors         TEXT,   -- JSON list of "Last FM"
  affiliations    TEXT,   -- JSON list (first affiliation strings)
  corpus_layer    TEXT,   -- A_seed | B_recall | C_manual
  scope_tags      TEXT,   -- JSON list of scope ids matched (MONDO/OMIM/ORPHA/genes)
  is_open_access  INTEGER,
  license         TEXT,
  in_epmc         INTEGER,
  has_suppl       INTEGER,
  fulltext_tier   TEXT,   -- best tier reached: epmc | unpaywall | publisher | abstract
  fulltext_path   TEXT,
  edat            TEXT,   -- PubMed entry date (for incremental runs)
  added_at        REAL,
  updated_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_documents_year ON documents(pub_year);
CREATE INDEX IF NOT EXISTS idx_documents_tier ON documents(fulltext_tier);
CREATE INDEX IF NOT EXISTS idx_documents_doi ON documents(doi);

CREATE TABLE IF NOT EXISTS retrieval (
  pmid        TEXT NOT NULL,
  tier        TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  path        TEXT,
  status      TEXT,
  attempted_at REAL,
  PRIMARY KEY (pmid, tier)
);

CREATE TABLE IF NOT EXISTS triage (
  pmid            TEXT PRIMARY KEY,
  in_scope        INTEGER,
  n_individuals   INTEGER,
  individual_ids  TEXT,   -- JSON list of identifiers as used in the text
  is_rereport     INTEGER,
  reasons         TEXT,
  model           TEXT,
  prompt_version  TEXT,
  decided_at      REAL
);

CREATE TABLE IF NOT EXISTS individuals (
  record_id       TEXT PRIMARY KEY,   -- PMID_<pmid>_<slug>
  pmid            TEXT NOT NULL,
  local_id        TEXT,               -- identifier used in the paper
  record          TEXT NOT NULL,      -- JSON, the full validated record
  phenopacket     TEXT,               -- JSON, GA4GH phenopacket v2
  valid           INTEGER,
  validation      TEXT,               -- JSON list of issues
  confidence      REAL,
  model           TEXT,
  prompt_version  TEXT,
  extracted_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_individuals_pmid ON individuals(pmid);

CREATE TABLE IF NOT EXISTS links (
  record_a  TEXT NOT NULL,
  record_b  TEXT NOT NULL,
  relation  TEXT NOT NULL,   -- same_individual_as | relative_of
  evidence  TEXT,
  score     REAL,
  PRIMARY KEY (record_a, record_b, relation)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  command     TEXT,
  args        TEXT,
  version     TEXT,
  started_at  REAL,
  finished_at REAL,
  summary     TEXT
);
"""


class Catalogue:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- generic helpers ----------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def upsert_document(self, doc: dict[str, Any]) -> None:
        """Insert or update a document; JSON-encodes list/dict fields; keeps added_at."""
        row = dict(doc)
        for k in ("pub_types", "mesh", "keywords", "authors", "affiliations", "scope_tags"):
            if k in row and not isinstance(row[k], (str, type(None))):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        now = time.time()
        row.setdefault("updated_at", now)
        cols = [c for c in row.keys() if c != "added_at"]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "pmid")
        sql = (
            f"INSERT INTO documents ({','.join(cols)}, added_at) VALUES ({placeholders}, ?) "
            f"ON CONFLICT(pmid) DO UPDATE SET {updates}"
        )
        with self.tx() as c:
            c.execute(sql, [row[k] for k in cols] + [now])

    def get_document(self, pmid: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM documents WHERE pmid=?", (pmid,)).fetchone()
        return _decode(dict(r)) if r else None

    def iter_documents(self, where: str = "1=1", params: Iterable[Any] = ()) -> Iterator[dict[str, Any]]:
        for r in self.conn.execute(f"SELECT * FROM documents WHERE {where}", tuple(params)):
            yield _decode(dict(r))

    def count(self, table: str, where: str = "1=1", params: Iterable[Any] = ()) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", tuple(params)).fetchone()[0]

    def record_retrieval(self, pmid: str, tier: str, ok: bool, path: str | None, status: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO retrieval (pmid,tier,ok,path,status,attempted_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(pmid,tier) DO UPDATE SET ok=excluded.ok,path=excluded.path,status=excluded.status,attempted_at=excluded.attempted_at",
                (pmid, tier, int(ok), path, status, time.time()),
            )
            if ok:
                c.execute(
                    "UPDATE documents SET fulltext_tier=?, fulltext_path=?, updated_at=? WHERE pmid=? "
                    "AND (fulltext_tier IS NULL OR fulltext_tier='abstract' OR fulltext_tier=?)",
                    (tier, path, time.time(), pmid, tier),
                )

    def retrieval_attempted(self, pmid: str, tier: str) -> bool:
        return self.conn.execute("SELECT 1 FROM retrieval WHERE pmid=? AND tier=?", (pmid, tier)).fetchone() is not None

    def set_triage(self, pmid: str, **fields: Any) -> None:
        fields["pmid"] = pmid
        fields.setdefault("decided_at", time.time())
        if isinstance(fields.get("individual_ids"), list):
            fields["individual_ids"] = json.dumps(fields["individual_ids"], ensure_ascii=False)
        cols = list(fields.keys())
        with self.tx() as c:
            c.execute(
                f"INSERT INTO triage ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) "
                f"ON CONFLICT(pmid) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in cols if k != "pmid"),
                [fields[k] for k in cols],
            )

    def put_individual(self, record_id: str, pmid: str, local_id: str | None, record: dict, phenopacket: dict | None,
                       valid: bool, validation: list, confidence: float | None, model: str, prompt_version: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO individuals (record_id,pmid,local_id,record,phenopacket,valid,validation,confidence,model,prompt_version,extracted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET record=excluded.record, phenopacket=excluded.phenopacket, "
                "valid=excluded.valid, validation=excluded.validation, confidence=excluded.confidence, model=excluded.model, "
                "prompt_version=excluded.prompt_version, extracted_at=excluded.extracted_at",
                (record_id, pmid, local_id, json.dumps(record, ensure_ascii=False),
                 json.dumps(phenopacket, ensure_ascii=False) if phenopacket else None,
                 int(valid), json.dumps(validation, ensure_ascii=False), confidence, model, prompt_version, time.time()),
            )

    def iter_individuals(self, where: str = "1=1", params: Iterable[Any] = ()) -> Iterator[dict[str, Any]]:
        for r in self.conn.execute(f"SELECT * FROM individuals WHERE {where}", tuple(params)):
            d = dict(r)
            d["record"] = json.loads(d["record"])
            d["phenopacket"] = json.loads(d["phenopacket"]) if d.get("phenopacket") else None
            d["validation"] = json.loads(d["validation"]) if d.get("validation") else []
            yield d

    def add_link(self, a: str, b: str, relation: str, evidence: str, score: float) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO links VALUES (?,?,?,?,?)", (a, b, relation, evidence, score))

    def start_run(self, run_id: str, command: str, args: dict, version: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO runs (run_id,command,args,version,started_at) VALUES (?,?,?,?,?)",
                      (run_id, command, json.dumps(args, default=str), version, time.time()))

    def finish_run(self, run_id: str, summary: dict) -> None:
        with self.tx() as c:
            c.execute("UPDATE runs SET finished_at=?, summary=? WHERE run_id=?", (time.time(), json.dumps(summary, default=str), run_id))

    def status(self) -> dict[str, Any]:
        q = self.conn.execute
        out = {
            "documents": q("SELECT COUNT(*) FROM documents").fetchone()[0],
            "by_tier": {r[0] or "none": r[1] for r in q("SELECT fulltext_tier, COUNT(*) FROM documents GROUP BY fulltext_tier")},
            "by_layer": {r[0] or "none": r[1] for r in q("SELECT corpus_layer, COUNT(*) FROM documents GROUP BY corpus_layer")},
            "open_access": q("SELECT COUNT(*) FROM documents WHERE is_open_access=1").fetchone()[0],
            "in_epmc": q("SELECT COUNT(*) FROM documents WHERE in_epmc=1").fetchone()[0],
            "triaged": q("SELECT COUNT(*) FROM triage").fetchone()[0],
            "triaged_in_scope": q("SELECT COUNT(*) FROM triage WHERE in_scope=1").fetchone()[0],
            "individuals": q("SELECT COUNT(*) FROM individuals").fetchone()[0],
            "individuals_valid": q("SELECT COUNT(*) FROM individuals WHERE valid=1").fetchone()[0],
            "links": q("SELECT COUNT(*) FROM links").fetchone()[0],
        }
        return out


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    for k in ("pub_types", "mesh", "keywords", "authors", "affiliations", "scope_tags"):
        v = row.get(k)
        if isinstance(v, str):
            try:
                row[k] = json.loads(v)
            except json.JSONDecodeError:
                pass
    return row

"""PMC-Patients (Zhao et al., Sci Data 2023; V2 = 250k summaries from the 2024 PMC baseline).

Streams the JSON (list of dicts) without loading it into memory and builds:
  patients.parquet / patients.csv   one row per patient summary with derived columns
  summary tables                     year, licence group, sex, age band, patients-per-article,
                                     summary length, diseases mentioned in titles, IEM-scope hits

Derived columns
  approx_year   from the PMID (PMIDs are assigned roughly chronologically; anchor table below, ±1 year)
  licence_group from file_path: comm (CC BY-like), noncomm (CC BY-NC-like), other (author manuscripts etc.)
  age_years     the age list [[value, unit], ...] collapsed to years
  age_band      neonate <28 d, infant <1 y, child 1-<12 y, adolescent 12-<18 y, adult 18-<65 y, older adult >=65 y
  n_words       words in the summary
  title_mondo   MONDO ids whose label/synonym occurs in the title (n-gram lookup, exact after normalisation)
  iem           any title_mondo in the casecorpus IEM scope, or an IEM gene symbol in title/summary
"""
from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

# PMID -> approximate year anchors (first PMID assigned in that year, rounded); linear interpolation between them
_PMID_ANCHORS = [  # approximate first PMID of each year (PubMed assigns PMIDs roughly in order of receipt)
    (1_000, 1946), (10_000_000, 1999), (11_150_000, 2001), (12_500_000, 2003), (14_700_000, 2004), (15_600_000, 2005),
    (16_400_000, 2006), (17_200_000, 2007), (18_150_000, 2008), (19_100_000, 2009), (20_050_000, 2010), (21_200_000, 2011),
    (22_200_000, 2012), (23_300_000, 2013), (24_400_000, 2014), (25_550_000, 2015), (26_700_000, 2016), (28_050_000, 2017),
    (29_300_000, 2018), (30_600_000, 2019), (31_900_000, 2020), (33_400_000, 2021), (34_900_000, 2022), (36_600_000, 2023),
    (38_200_000, 2024), (39_700_000, 2025), (41_000_000, 2026),
]


def pmid_year(pmid: int | str | None) -> float | None:
    try:
        p = int(pmid)
    except (TypeError, ValueError):
        return None
    for (p0, y0), (p1, y1) in zip(_PMID_ANCHORS, _PMID_ANCHORS[1:]):
        if p0 <= p < p1:
            return round(y0 + (p - p0) / (p1 - p0) * (y1 - y0), 1)
    return float(_PMID_ANCHORS[-1][1]) if p >= _PMID_ANCHORS[-1][0] else None


_UNIT_YEARS = {"year": 1.0, "years": 1.0, "month": 1 / 12, "months": 1 / 12, "week": 7 / 365.25, "weeks": 7 / 365.25, "day": 1 / 365.25, "days": 1 / 365.25, "hour": 1 / 8766, "hours": 1 / 8766}


def age_years(age: Any) -> float | None:
    """[[60.0, 'year']] -> 60.0 ; [[1.0,'year'],[2.0,'month']] -> 1.17 ; unparsable -> None"""
    if isinstance(age, str):
        try:
            age = json.loads(age.replace("'", '"').replace("(", "[").replace(")", "]"))
        except json.JSONDecodeError:
            return None
    if not isinstance(age, list):
        return None
    total = 0.0
    ok = False
    for item in age:
        try:
            v, u = item[0], str(item[1]).lower()
        except (TypeError, IndexError):
            continue
        if u in _UNIT_YEARS:
            total += float(v) * _UNIT_YEARS[u]
            ok = True
    return round(total, 3) if ok else None


def age_band(y: float | None) -> str:
    if y is None:
        return "unknown"
    if y < 28 / 365.25:
        return "neonate (<28 d)"
    if y < 1:
        return "infant (<1 y)"
    if y < 12:
        return "child (1-<12 y)"
    if y < 18:
        return "adolescent (12-<18 y)"
    if y < 65:
        return "adult (18-<65 y)"
    return "older adult (>=65 y)"


_LIC = re.compile(r"(?:^|/)(?:oa_)?(noncomm|comm|other)/")


def licence_group(file_path: str | None) -> str:
    m = _LIC.search((file_path or "").lower())
    return m.group(1) if m else "unknown"


def iter_patients(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a (possibly gzipped) JSON list or a CSV."""
    if path.suffix == ".csv":
        for chunk in pd.read_csv(path, chunksize=20_000, dtype=str):
            for rec in chunk.to_dict("records"):
                yield rec
        return
    import ijson
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as fh:
        for rec in ijson.items(fh, "item"):
            yield rec


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-']*")


class TitleDiseaseTagger:
    """Exact n-gram lookup of MONDO labels/synonyms (normalised) in titles; cheap enough for 250k titles."""

    _GENERIC = {
        # bare heads that name a class, not a diagnosis
        "disease", "disorder", "syndrome", "infection", "cancer", "carcinoma", "adenocarcinoma", "tumor", "tumour", "neoplasm", "lesion", "mass",
        "injury", "pain", "fever", "death", "shock", "case report", "rare disease", "complication", "complications", "condition",
        # qualifiers that MONDO lists as synonyms of grouping classes
        "congenital", "idiopathic", "disseminated", "iatrogenic", "hereditary", "familial", "acquired", "malignant", "benign", "chronic", "acute",
        "recurrent", "refractory", "metastatic", "bilateral", "unilateral", "primary", "secondary", "juvenile", "infantile", "neonatal", "adult-onset",
        "late-onset", "early-onset", "sporadic", "atypical", "classic", "complicated", "uncomplicated", "localized", "generalized", "systemic",
        # odd grouping-class synonyms that swallow common words
        "kidney disorder", "immunodeficiency disease", "heart disorder", "liver disorder", "lung disorder", "skin disorder", "eye disorder", "bone disorder",
        "blood disorder", "brain disorder", "nervous system disorder", "vascular disease", "genetic disease", "diseases", "disorders", "cancers", "tumors",
        "tumours", "neoplasms", "infections", "syndromes", "illness", "human disease", "disease or disorder", "pediatric disease", "adult disease",
    }

    def __init__(self, mondo_index: dict[str, str], labels: dict[str, str], max_n: int = 6, min_len: int = 8):
        from ..ground.mondo import _norm
        # ids whose own label is a generic head/grouping class are never reported, whichever synonym matched
        generic_ids = {mid for mid, lab in labels.items() if _norm(lab) in self._GENERIC}
        # keys: multi-word >= min_len chars; single words >= 9 chars; a few generic heads dropped
        self.exact = {k: v for k, v in mondo_index.items()
                      if len(k) >= 6 and (" " in k and len(k) >= min_len or len(k) >= 9 or any(ch.isdigit() or ch == "-" for ch in k))
                      and k not in self._GENERIC and v not in generic_ids}
        self.labels = labels
        self.max_n = max_n

    def tag(self, text: str) -> list[str]:
        from ..ground.mondo import _norm
        toks = _norm(text).split()
        found: set[str] = set()
        for n in range(self.max_n, 0, -1):
            for i in range(len(toks) - n + 1):
                key = " ".join(toks[i:i + n])
                mid = self.exact.get(key)
                if mid:
                    found.add(mid)
        # drop ancestors-by-name: keep only the longest matches (approximation: remove ids whose label is a substring of another matched label)
        labs = {m: self.labels.get(m, "") for m in found}
        keep = {m for m in found if not any(m != o and labs[m] and labs[m].lower() in labs[o].lower() for o in found)}
        return sorted(keep)


def build_overview(src: Path, out: Path, ontologies: Path | None = None, scope_dir: Path | None = None, limit: int | None = None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    tagger = None
    iem_mondo: set[str] = set()
    gene_re = None
    if ontologies and (ontologies / "mondo.json").exists():
        from ..ground.mondo import MondoGrounder
        mg = MondoGrounder(ontologies / "mondo.json", cache=ontologies / "mondo_index.json")
        tagger = TitleDiseaseTagger(mg.exact, mg.label)
    if scope_dir and (scope_dir / "scope.json").exists():
        scope = json.load(open(scope_dir / "scope.json"))
        iem_mondo = {d["mondo"] for d in scope["diseases"]}
        genes = sorted({g for d in scope["diseases"] for g in d.get("genes", []) if len(g) >= 4}, key=len, reverse=True)
        gene_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(g) for g in genes) + r")(?![A-Za-z0-9])") if genes else None

    rows: list[dict[str, Any]] = []
    per_article: Counter = Counter()
    for i, rec in enumerate(iter_patients(src)):
        if limit and i >= limit:
            break
        pmid = str(rec.get("PMID") or "")
        per_article[pmid] += 1
        title = rec.get("title") or ""
        text = rec.get("patient") or ""
        ay = age_years(rec.get("age"))
        mondos = tagger.tag(title) if tagger else []
        genes_hit = sorted(set(gene_re.findall(title + " " + text[:4000]))) if gene_re else []
        rel = rec.get("relevant_articles")
        sim = rec.get("similar_patients")
        rows.append({
            "patient_uid": rec.get("patient_uid"), "pmid": pmid, "approx_year": pmid_year(pmid), "licence_group": licence_group(rec.get("file_path")),
            "gender": rec.get("gender"), "age_years": ay, "age_band": age_band(ay), "age_raw": json.dumps(rec.get("age"), default=float) if not isinstance(rec.get("age"), str) else rec.get("age"),
            "n_chars": len(text), "n_words": len(_WORD.findall(text)), "title": title,
            "title_mondo": ";".join(mondos), "title_mondo_labels": ";".join(tagger.labels.get(m, "") for m in mondos) if tagger else "",
            "iem_title": bool(set(mondos) & iem_mondo), "iem_genes": ";".join(genes_hit), "iem": bool(set(mondos) & iem_mondo) or bool(genes_hit),
            "n_relevant_articles": len(rel) if isinstance(rel, dict) else (len(json.loads(rel)) if isinstance(rel, str) and rel.startswith("{") else None),
            "n_similar_patients": len(sim) if isinstance(sim, dict) else (len(json.loads(sim)) if isinstance(sim, str) and sim.startswith("{") else None),
            "summary": text,
        })
    df = pd.DataFrame(rows)
    df["patients_in_article"] = df["pmid"].map(per_article)
    df.to_parquet(out / "patients.parquet", index=False)
    df.drop(columns=["summary"]).to_csv(out / "patients.csv", index=False)

    tables: dict[str, pd.DataFrame] = {}
    tables["by_year"] = df.assign(year=df["approx_year"].round()).groupby("year").agg(patients=("patient_uid", "count"), articles=("pmid", "nunique")).reset_index()
    tables["by_licence"] = df.groupby("licence_group").agg(patients=("patient_uid", "count"), articles=("pmid", "nunique")).reset_index()
    tables["by_sex"] = df.groupby(df["gender"].fillna("unknown")).agg(patients=("patient_uid", "count")).reset_index()
    tables["by_age_band"] = df.groupby("age_band").agg(patients=("patient_uid", "count"), median_age_years=("age_years", "median")).reset_index()
    tables["by_sex_age"] = df.pivot_table(index="age_band", columns=df["gender"].fillna("unknown"), values="patient_uid", aggfunc="count", fill_value=0).reset_index()
    ppa = df.groupby("pmid").size()
    tables["patients_per_article"] = ppa.value_counts().sort_index().rename_axis("patients_in_article").reset_index(name="articles")
    tables["summary_length"] = df["n_words"].describe(percentiles=[.05, .25, .5, .75, .95]).rename_axis("statistic").reset_index(name="words")
    dis = Counter()
    for s in df["title_mondo_labels"]:
        for lab in filter(None, s.split(";")):
            dis[lab] += 1
    tables["top_title_diseases"] = pd.DataFrame(dis.most_common(300), columns=["disease_in_title", "patients"])
    tables["iem"] = pd.DataFrame([{"criterion": "IEM disease named in title", "patients": int(df["iem_title"].sum())},
                                  {"criterion": "IEM-scope gene symbol in title/summary", "patients": int((df["iem_genes"] != "").sum())},
                                  {"criterion": "either", "patients": int(df["iem"].sum())},
                                  {"criterion": "total", "patients": len(df)}])
    for name, t in tables.items():
        t.to_csv(out / f"{name}.csv", index=False)
    stats = {"patients": len(df), "articles": int(df["pmid"].nunique()), "with_age": int(df["age_years"].notna().sum()), "with_sex": int(df["gender"].notna().sum()),
             "median_words": float(df["n_words"].median()), "iem_any": int(df["iem"].sum()), "titles_with_disease": int((df["title_mondo"] != "").sum()),
             "year_range": [float(df["approx_year"].min()), float(df["approx_year"].max())], "out": str(out)}
    (out / "overview.json").write_text(json.dumps(stats, indent=1))
    return stats

"""Parse PubMed efetch XML (PubmedArticleSet) into flat document dicts."""
from __future__ import annotations

import re
from typing import Any, Iterator

from lxml import etree


def _text(el: etree._Element | None) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _pub_date(article: etree._Element) -> tuple[int | None, str | None]:
    # Prefer ArticleDate (electronic), then JournalIssue/PubDate
    for xp in (".//ArticleDate", ".//Journal/JournalIssue/PubDate"):
        d = article.find(xp)
        if d is None:
            continue
        y = _text(d.find("Year"))
        m = _text(d.find("Month")) or "01"
        day = _text(d.find("Day")) or "01"
        if not y:
            medline = _text(d.find("MedlineDate"))
            mm = re.match(r"(\d{4})", medline)
            if mm:
                return int(mm.group(1)), mm.group(1)
            continue
        months = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06", "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
        m = months.get(m[:3], m if m.isdigit() else "01")
        return int(y), f"{y}-{int(m):02d}-{int(day):02d}"
    return None, None


def parse_pubmed_xml(xml_bytes: bytes, recover: bool = False) -> Iterator[dict[str, Any]]:
    """recover=True tolerates damaged XML (e.g. text copied from a browser view where entities were decoded)."""
    root = etree.fromstring(xml_bytes, parser=etree.XMLParser(recover=True, huge_tree=True) if recover else None)
    for pa in root.iter("PubmedArticle"):
        med = pa.find("MedlineCitation")
        art = med.find("Article")
        pmid = _text(med.find("PMID"))
        ids = {}
        idlist = pa.find("PubmedData/ArticleIdList")
        for aid in (idlist.findall("ArticleId") if idlist is not None else []):
            ids[aid.get("IdType")] = _text(aid)
        pmc = ids.get("pmc")
        if pmc and not pmc.startswith("PMC"):
            pmc = "PMC" + pmc
        abstract_parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            t = _text(ab)
            abstract_parts.append(f"{label}: {t}" if label else t)
        year, date = _pub_date(art)
        mesh = []
        for mh in med.findall(".//MeshHeadingList/MeshHeading"):
            d = mh.find("DescriptorName")
            mesh.append({
                "descriptor": _text(d),
                "ui": d.get("UI") if d is not None else None,
                "major": (d.get("MajorTopicYN") == "Y") if d is not None else False,
                "qualifiers": [_text(q) for q in mh.findall("QualifierName")],
            })
        authors, affils = [], []
        for a in art.findall(".//AuthorList/Author"):
            last, init = _text(a.find("LastName")), _text(a.find("Initials"))
            coll = _text(a.find("CollectiveName"))
            authors.append(f"{last} {init}".strip() if last else coll)
            for af in a.findall(".//AffiliationInfo/Affiliation"):
                t = _text(af)
                if t and t not in affils:
                    affils.append(t)
        edat = None
        for pd in pa.findall(".//PubMedPubDate"):
            if pd.get("PubStatus") == "entrez":
                edat = f"{_text(pd.find('Year'))}-{int(_text(pd.find('Month')) or 1):02d}-{int(_text(pd.find('Day')) or 1):02d}"
        yield {
            "pmid": pmid,
            "pmcid": pmc,
            "doi": (ids.get("doi") or "").lower() or None,
            "title": _text(art.find("ArticleTitle")),
            "abstract": "\n".join(abstract_parts) or None,
            "journal": _text(art.find("Journal/Title")),
            "journal_iso": _text(art.find("Journal/ISOAbbreviation")),
            "issn": _text(art.find("Journal/ISSN")) or None,
            "pub_year": year,
            "pub_date": date,
            "language": _text(art.find("Language")) or None,
            "pub_types": [_text(pt) for pt in art.findall(".//PublicationTypeList/PublicationType")],
            "mesh": mesh,
            "keywords": [_text(k) for k in med.findall(".//KeywordList/Keyword")],
            "authors": authors,
            "affiliations": affils[:5],
            "edat": edat,
        }

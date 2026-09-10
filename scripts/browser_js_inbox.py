"""Save full-text XML returned by the browser javascript_tool ({pmcid: {status, xml}}) into raw/<pmid>/epmc.xml.
Usage: python browser_js_inbox.py <tool-result.json> <catalogue.sqlite>"""
import json, re, sys, sqlite3
from pathlib import Path
src, dbp = sys.argv[1], sys.argv[2]
arr = json.load(open(src))
text = "".join(b.get("text", "") for b in arr if b.get("type") == "text")
# the result is the JSON value, possibly followed by "(captured at origin ...)" and tab context
m = re.match(r"\s*(\{.*\})\s*(?:\(captured at origin[^\n]*)?", text, re.S)
payload = json.loads(m.group(1)) if m else json.loads(text)
conn = sqlite3.connect(dbp)
raw_root = Path(dbp).parent / "raw"
for pmcid, v in payload.items():
    row = conn.execute("SELECT pmid FROM documents WHERE pmcid=?", (pmcid,)).fetchone()
    if not row:
        print(pmcid, "not in catalogue"); continue
    pmid = row[0]
    if v.get("status") != 200 or "<body" not in v.get("xml", ""):
        print(pmcid, pmid, "no full text", v.get("status")); continue
    d = raw_root / pmid; d.mkdir(parents=True, exist_ok=True)
    (d / "epmc.xml").write_text(v["xml"])
    conn.execute("INSERT OR REPLACE INTO retrieval (pmid,tier,ok,path,status,attempted_at) VALUES (?,?,?,?,?,strftime('%s','now'))", (pmid, "epmc", 1, str(d / "epmc.xml"), "ok (pmc efetch via browser)"))
    conn.execute("UPDATE documents SET fulltext_tier='epmc', fulltext_path=? WHERE pmid=?", (str(d / "epmc.xml"), pmid))
    print(pmcid, pmid, len(v["xml"]), "chars saved")
conn.commit()

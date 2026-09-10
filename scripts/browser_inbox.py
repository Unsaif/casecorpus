"""Turn a saved browser get_page_text result (JSON array of {type,text}) into a raw file.
Usage: python browser_inbox.py <tool-result.json> <out-path>
The page text starts after the '---' header line the tool adds; trailing 'Tab Context' is stripped."""
import json, re, sys
src, out = sys.argv[1], sys.argv[2]
arr = json.load(open(src))
text = "".join(b.get("text", "") for b in arr if b.get("type") == "text")
m = re.search(r"\n---\n", text)
if m:
    text = text[m.end():]
text = re.split(r"\n\nTab Context:\n", text)[0]
open(out, "w").write(text)
print(out, len(text), "chars")

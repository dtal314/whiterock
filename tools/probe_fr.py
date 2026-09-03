"""Quick check that one Federal Register window fetches and normalizes."""
from datetime import date

from whiterock.sources import federal_register as fr
from whiterock.util import Http

http = Http(delay_s=0.2)
docs = fr.fetch_window(http, date(2026, 8, 1), date(2026, 8, 20))
print(len(docs), "docs")
for d in docs[:3]:
    print(d["publication_date"], d["type"], d["pres_doc_type"], d["agencies"][:2], d["title"][:70])
pi = fr.fetch_public_inspection()
print(len(pi), "public inspection docs;", pi[0] if pi else None)

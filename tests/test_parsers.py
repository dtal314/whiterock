from whiterock.sources.house_clerk import parse_ptr_text
from whiterock.sources.senate_efd import parse_ptr_html

HOUSE_TEXT = """Filing ID #20034916
Name: Hon. Robert J. Wittman
Status: Member
State/District: VA01
ID Owner Asset Transaction Date Notification Amount Cap.
Type Date Gains >
$200?
SP Crown Castle Inc. Common Stock S 06/30/2026 07/02/2026 $1,001 - $15,000
(CCI) [ST]
F      S     : New
S          O : Morgan Stanley
Treasury Bill (3-Month, Matures P 07/13/2026 07/13/2026 $15,001 -
10/15/2026) [GS] $50,000
F      S     : New
JT NVIDIA Corporation - Common Stock (NVDA) S (partial) 06/12/2026 06/13/2026 $50,001 - $100,000
[ST]
D: Partial sale of holdings
* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx.
"""


def test_house_ptr_rows():
    txs = parse_ptr_text(HOUSE_TEXT)
    assert len(txs) == 3
    a, b, c = txs
    assert a["owner"] == "spouse" and a["ticker"] == "CCI" and a["tx_type"] == "sale"
    assert a["tx_date"] == "2026-06-30" and a["amount_low"] == 1001 and a["amount_high"] == 15000
    assert b["ticker"] is None and b["asset_type"] == "GS" and b["amount_high"] == 50000
    assert "Matures 10/15/2026" in b["asset"]
    assert c["owner"] == "joint" and c["tx_type"] == "sale_partial" and c["ticker"] == "NVDA"
    assert c["amount_low"] == 50001


SENATE_HTML = """<table><thead><tr><th>#</th><th>Transaction Date</th><th>Owner</th><th>Ticker</th>
<th>Asset Name</th><th>Asset Type</th><th>Type</th><th>Amount</th><th>Comment</th></tr></thead>
<tbody>
<tr><td>1</td><td>08/13/2026</td><td>Spouse</td><td>NVDA</td><td>NVIDIA Corporation</td><td>Stock</td>
<td>Sale (Partial)</td><td>$1,001 - $15,000</td><td>--</td></tr>
<tr><td>2</td><td>08/06/2026</td><td>Self</td><td>--</td><td>Sandoz Group AG ADR</td><td>Stock</td>
<td>Purchase</td><td>Over $50,000,000</td><td>note</td></tr>
</tbody></table>"""


def test_senate_ptr_rows():
    rows = parse_ptr_html(SENATE_HTML)
    assert len(rows) == 2
    assert rows[0]["owner"] == "spouse" and rows[0]["ticker"] == "NVDA" and rows[0]["tx_type"] == "sale_partial"
    assert rows[0]["tx_date"] == "2026-08-13" and rows[0]["amount_high"] == 15000
    assert rows[1]["ticker"] is None and rows[1]["tx_type"] == "purchase" and rows[1]["amount_high"] is None
    assert rows[1]["comment"] == "note"

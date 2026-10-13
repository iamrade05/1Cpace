"""Read-only banking enrichment from individual Itensity member pages."""
from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup


class AmbiguousBanking(ValueError):
    """More than one source account needs staff selection."""


def clean(value):
    value = str(value or "").strip()
    return "" if value.upper() in {"N/A", "NA", "NONE", "NULL", "-"} else value


def account_type(value):
    key = re.sub(r"[^a-z0-9]", "", clean(value).lower())
    return {"1": "Cheque/Current", "2": "Savings", "3": "Transmission",
            "cheque": "Cheque/Current", "current": "Cheque/Current",
            "chequecurrent": "Cheque/Current", "savings": "Savings",
            "saving": "Savings", "transmission": "Transmission"}.get(key, "")


def parse_banking_page(html, ref):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id="bankDetails")
    if container is None:
        raise ValueError("Banking page unavailable or session expired")
    headers = [re.sub(r"[^a-z0-9]", "", c.get_text().lower())
               for c in container.select("th")]
    rows = [r.find_all("td", recursive=False) for r in container.select("tr")]
    rows = [r for r in rows if len(r) == len(headers) and r]
    if len(rows) > 1:
        raise AmbiguousBanking("Multiple source banking rows require review")
    if len(rows) != 1:
        raise ValueError("Expected one member banking row")
    values = dict(zip(headers, [clean(c.get_text(" ", strip=True)) for c in rows[0]]))
    def get(*keys):
        return next((values[k] for k in keys if values.get(k)), "")
    number = re.sub(r"\s", "", get("accnumber", "accountnumber", "accno", "accountno"))
    branch = re.sub(r"\s", "", get("accbranch", "branchcode", "branch"))
    return {"id": str(ref), "bank": get("bank", "bankname"),
            "accname": get("accname", "accountholder", "accountholdername", "accountname", "name"),
            "accno": number if number.isdigit() else "",
            "branch": branch if branch.isdigit() and len(branch) == 6 else "",
            "account_type": account_type(get("acctype", "accounttype")),
            "debitdate": get("debitdate", "debitorderdate")}


def enrich_unverified_banking(page, base, roster, banking):
    stats = {"attempted": 0, "retrieved": 0, "missing": 0, "failed": 0,
             "needs_review": 0, "review_refs": []}
    for row in roster:
        if str(row.get("status", "")).lower() != "unverified" or row.get("role") != "Member":
            continue
        ref = str(row.get("userid") or "").strip()
        if not ref.isdigit():
            stats["failed"] += 1
            continue
        stats["attempted"] += 1
        try:
            response = page.request.get(base + "/Debit_details.php?" + urlencode({"id": ref}), timeout=20000)
            if not response.ok:
                raise ValueError("Banking read failed")
            details = parse_banking_page(response.text(), ref)
            previous = dict(banking.get(ref) or {})
            previous.update({k: v for k, v in details.items() if v})
            banking[ref] = previous
            stats["retrieved" if details["accno"] else "missing"] += 1
        except AmbiguousBanking:
            stats["needs_review"] += 1
            stats["review_refs"].append(ref)
        except Exception:
            # Preserve the roster import and any existing banking on a partial outage.
            stats["failed"] += 1
    return stats

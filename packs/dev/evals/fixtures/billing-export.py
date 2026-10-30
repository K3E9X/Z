"""Monthly invoice export for the finance team (CSV, one line per invoice)."""

import csv
import io


def export_invoices(invoices: list[dict[str, object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["invoice", "customer", "amount_eur", "issued"])
    for inv in invoices:
        writer.writerow([inv["id"], inv["customer"], f"{float(str(inv['amount'])):.2f}", inv["issued"]])
    return out.getvalue()

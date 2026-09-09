"""Automatic delivery of newly issued statements using the existing audited service.

This module does not calculate expenses, generate PDFs, or access SMTP directly.
The company-scoped delivery service owns recipient validation, immutable payloads,
transactional claims, and delivery history. No automatic retries are performed.
"""
import os
from pathlib import Path

from dotenv import dotenv_values

import statement_delivery as delivery

AUTO_EMAIL_FLAG = "KOINOXRISTA_AUTO_EMAIL_AFTER_ISSUE"


def auto_email_enabled():
    """Require an explicit deployment opt-in as well as the SMTP safety switch."""
    env_file = Path(delivery.__file__).resolve().parent / ".env"
    values = dotenv_values(env_file) if env_file.is_file() else {}
    value = values.get(AUTO_EMAIL_FLAG)
    if value is None:
        value = os.environ.get(AUTO_EMAIL_FLAG, "")
    return str(value).strip().lower() == "true" and delivery.smtp_ready()


def eligible_recipients(preview_data):
    """Select only verified contacts with no delivery record for this revision."""
    eligible = []
    skipped = []
    for entry in preview_data["entries"]:
        if entry["delivery"] is not None:
            reason = "Υπάρχει ήδη καταγραφή αποστολής."
        elif not entry["enabled"] or not entry["email"] or not entry["tenant_name"]:
            reason = "Δεν υπάρχουν πλήρη, ενεργά στοιχεία επικοινωνίας."
        elif not entry["name_matches"]:
            reason = "Το όνομα διαφέρει από το εκδοθέν PDF."
        else:
            eligible.append(entry)
            continue
        skipped.append({"code": entry["code"], "reason": reason})
    return eligible, skipped


def send_after_issue(building_id, issued):
    """Send a new revision once; never resend an existing or reused revision.

    Call only after issue_statement has committed. All eligible deliveries are
    prepared before network submission. Failures never roll back the statement.
    The caller must retain the returned status and direct the user to history.
    """
    if not auto_email_enabled():
        return {"enabled": False, "results": [], "skipped": [], "error": ""}
    if issued.get("reused"):
        return {"enabled": True, "reused": True, "results": [], "skipped": [], "error": ""}

    result = {"enabled": True, "reused": False, "results": [], "skipped": [],
              "prepared": 0, "error": ""}
    try:
        data = delivery.preview(building_id, issued["id"])
        eligible, result["skipped"] = eligible_recipients(data)
        if not eligible:
            return result
        # The same immutable fingerprint and transactional duplicate checks used
        # by the manual UI are applied to each bounded batch.
        ids = []
        for start in range(0, len(eligible), delivery.MAX_BATCH):
            selected = [e["apartment_id"] for e in eligible[start:start + delivery.MAX_BATCH]]
            fingerprint = delivery.approval_fingerprint(data, selected)
            ids.extend(delivery.prepare(building_id, issued["id"], selected, fingerprint))
        result["prepared"] = len(ids)
        for start in range(0, len(ids), delivery.MAX_BATCH):
            batch = ids[start:start + delivery.MAX_BATCH]
            result["results"].extend(delivery.send_prepared(building_id, batch))
    except Exception:
        # Do not retry: the SMTP server may have accepted a message already.
        # The existing delivery history is the authoritative recovery surface.
        result["error"] = "Η αυτόματη αποστολή διακόπηκε. Έλεγξε το Ιστορικό αποστολών πριν από οποιαδήποτε επανάληψη."
    return result

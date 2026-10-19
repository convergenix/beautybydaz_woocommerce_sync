from __future__ import annotations

import json

import frappe
from frappe.utils import now_datetime

from .core import safe_summary


def create_log(operation, direction, channel=None, external_id=None, idempotency_key=None, request_summary=None):
    doc = frappe.get_doc(
        {
            "doctype": "BBD Woo Sync Log",
            "operation": operation,
            "direction": direction,
            "channel": channel,
            "external_id": str(external_id or ""),
            "idempotency_key": idempotency_key,
            "status": "Running",
            "attempts": 1,
            "started_at": now_datetime(),
            "request_summary": json.dumps(safe_summary(request_summary or {}), default=str),
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def finish_log(doc, status, response=None, reference_doctype=None, reference_name=None, error=None, traceback_text=None):
    doc.status = status
    doc.completed_at = now_datetime()
    doc.response_summary = json.dumps(safe_summary(response or {}), default=str)
    doc.reference_doctype = reference_doctype
    doc.reference_name = reference_name
    doc.error = str(error or "")[:10000]
    doc.traceback = str(traceback_text or "")[:20000]
    doc.save(ignore_permissions=True)
    if status == "Failed":
        frappe.log_error(title=f"BBD Woo Sync {doc.operation} failed", message=doc.error + "\n" + doc.traceback)
        _send_failure_alert(doc)
    return doc


def _send_failure_alert(log_doc):
    try:
        settings = frappe.get_single("BBD Woo Sync Settings")
        recipients = [x.strip() for x in (settings.alert_recipients or "").split(",") if x.strip()]
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"Beauty By Daz Woo sync failure: {log_doc.operation}",
                message=f"Channel: {log_doc.channel or '-'}<br>External ID: {log_doc.external_id or '-'}<br>Error: {frappe.utils.escape_html(log_doc.error or '')}",
                delayed=False,
            )
    except Exception:
        frappe.log_error(title="BBD Woo Sync alert delivery failed", message=frappe.get_traceback())

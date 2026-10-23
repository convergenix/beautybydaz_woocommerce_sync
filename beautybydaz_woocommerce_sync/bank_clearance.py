from __future__ import annotations

import frappe
from frappe import _, msgprint
from frappe.utils import get_link_to_form, getdate

from erpnext.accounts.doctype.bank_clearance.bank_clearance import BankClearance


class SafeBankClearance(BankClearance):
    @frappe.whitelist()
    def update_clearance_date(self):
        payment_docs = []
        for row in self.get("payment_entries"):
            if row.payment_document not in payment_docs:
                payment_docs.append(row.payment_document)

        for doctype in payment_docs:
            frappe.has_permission(doctype, "write", throw=True)

        clearance_date_updated = False
        for row in self.get("payment_entries"):
            if row.clearance_date:
                if not row.payment_document:
                    frappe.throw(_("Row #{0}: Payment document is required to complete the transaction").format(row.idx))

                if row.cheque_date and getdate(row.clearance_date) < getdate(row.cheque_date):
                    frappe.throw(
                        _("Row #{0}: For {1} Clearance date {2} cannot be before Cheque Date {3}").format(
                            row.idx,
                            get_link_to_form(row.payment_document, row.payment_entry),
                            row.clearance_date,
                            row.cheque_date,
                        )
                    )

            if row.clearance_date or self.include_reconciled_entries:
                if not row.clearance_date:
                    row.clearance_date = None

                if row.payment_document == "Sales Invoice":
                    frappe.db.set_value(
                        "Sales Invoice Payment",
                        {"parent": row.payment_entry, "account": self.get("account"), "amount": [">", 0]},
                        "clearance_date",
                        row.clearance_date,
                    )
                elif row.payment_document == "Payment Entry":
                    submitted = frappe.db.sql(
                        """
                        SELECT name
                        FROM `tabPayment Entry`
                        WHERE name=%s AND docstatus=1
                        FOR UPDATE
                        """,
                        (row.payment_entry,),
                    )
                    if not submitted:
                        frappe.throw(
                            _("Payment Entry {0} is no longer submitted; its clearance date was not changed").format(
                                row.payment_entry
                            )
                        )
                    frappe.db.set_value("Payment Entry", row.payment_entry, "clearance_date", row.clearance_date)
                else:
                    frappe.db.set_value(row.payment_document, row.payment_entry, "clearance_date", row.clearance_date)

                clearance_date_updated = True

        if clearance_date_updated:
            self.get_payment_entries()
            msgprint(_("Clearance Date updated"))
        else:
            msgprint(_("Clearance Date not mentioned"))

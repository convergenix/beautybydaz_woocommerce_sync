import re

import frappe
from frappe.model.document import Document


class BBDWooChannel(Document):
    def autoname(self):
        self.name = self.alias

    def validate(self):
        self.alias = (self.alias or "").strip()
        if not re.fullmatch(r"[a-z0-9_\-]+", self.alias):
            frappe.throw("Channel alias may contain only lowercase letters, numbers, underscore and hyphen")
        self.base_url = (self.base_url or "").rstrip("/")
        if not self.base_url.startswith("https://"):
            frappe.throw("WooCommerce Base URL must use HTTPS")
        self.timeout_seconds = max(5, int(self.timeout_seconds or 30))
        self.max_retries = max(0, min(8, int(self.max_retries or 3)))

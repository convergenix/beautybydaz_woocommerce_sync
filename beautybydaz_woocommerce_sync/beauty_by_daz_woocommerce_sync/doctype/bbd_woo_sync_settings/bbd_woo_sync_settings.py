from frappe.model.document import Document


class BBDWooSyncSettings(Document):
    def validate(self):
        self.payment_entries_enabled = 0

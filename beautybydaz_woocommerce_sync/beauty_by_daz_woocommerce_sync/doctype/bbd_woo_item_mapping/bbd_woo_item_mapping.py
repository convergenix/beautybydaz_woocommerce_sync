import frappe
from frappe.model.document import Document


class BBDWooItemMapping(Document):
    def autoname(self):
        self.name = f"{self.channel}-{self.erp_item}"

    def validate(self):
        self.variation_id = int(self.variation_id or 0)
        duplicate_item = frappe.db.get_value(
            "BBD Woo Item Mapping",
            {"channel": self.channel, "erp_item": self.erp_item, "name": ["!=", self.name]},
            "name",
        )
        if duplicate_item:
            frappe.throw(f"Item {self.erp_item} already has mapping {duplicate_item} for channel {self.channel}")
        duplicate_product = frappe.db.get_value(
            "BBD Woo Item Mapping",
            {"channel": self.channel, "woo_product_id": self.woo_product_id, "variation_id": self.variation_id, "name": ["!=", self.name]},
            "name",
        )
        if duplicate_product:
            frappe.throw(f"Woo target is already mapped by {duplicate_product}")

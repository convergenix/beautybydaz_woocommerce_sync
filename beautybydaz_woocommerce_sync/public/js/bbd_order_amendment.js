frappe.ui.form.on("BBD Order Amendment", {
  refresh(frm) {
    if (frm.is_new()) return;

    if (frm.doc.status === "Pending") {
      frm.add_custom_button(__("Continue Amendment"), () => {
        frappe.route_options = { amendment: frm.doc.name };
        frappe.set_route("bbd-order-amendment");
      }, __("Actions"));
    }

    if (frm.doc.status === "Applied" && frappe.user_roles.includes("System Manager")) {
      frm.add_custom_button(__("Rollback Amendment"), () => {
        frappe.prompt(
          [
            {
              fieldname: "confirmation",
              label: __("Type the amendment ID to confirm"),
              fieldtype: "Data",
              reqd: 1,
              description: frm.doc.name,
            },
            {
              fieldname: "reason",
              label: __("Rollback reason"),
              fieldtype: "Small Text",
              reqd: 1,
            },
          ],
          async (values) => {
            const response = await frappe.call({
              method: "beautybydaz_woocommerce_sync.amendments.rollback_order_amendment",
              args: {
                amendment: frm.doc.name,
                confirmation: values.confirmation,
                reason: values.reason,
              },
              freeze: true,
              freeze_message: __("Rolling back WooCommerce and ERPNext documents…"),
            });
            const result = response.message || {};
            frappe.show_alert({ message: __("Amendment rolled back"), indicator: "green" });
            await frm.reload_doc();
            if (result.sales_order) {
              frappe.set_route("Form", "Sales Order", result.sales_order);
            }
          },
          __("Rollback Amendment"),
          __("Rollback")
        );
      }, __("Actions"));
    }
  },
});
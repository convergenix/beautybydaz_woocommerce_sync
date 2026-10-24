frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    const can_amend = ["System Manager", "BBD Order Amendment Manager"].some((role) =>
      (frappe.user_roles || []).includes(role)
    );
    const woo_order_id = frm.doc.custom_woocommerce_order_root_id || frm.doc.custom_woocommerce_order_id;
    if (!can_amend || frm.doc.docstatus !== 1 || !woo_order_id || !frm.doc.custom_woo_channel) {
      return;
    }

    frm.add_custom_button(__("Amend Website Order"), async () => {
      const response = await frappe.call({
        method: "beautybydaz_woocommerce_sync.amendments.start_sales_order_amendment",
        args: { sales_order: frm.doc.name },
        freeze: true,
        freeze_message: __("Validating WooCommerce order and ERP document chain…"),
      });
      const route = response.message || {};
      if (!route.amendment) {
        frappe.throw(__("The server did not create an amendment record."));
      }
      frappe.set_route("Form", "BBD Order Amendment", route.amendment);
    }, __("Actions"));
  },
});
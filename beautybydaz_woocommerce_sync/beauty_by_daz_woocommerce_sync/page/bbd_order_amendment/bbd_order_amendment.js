frappe.pages["bbd-order-amendment"].on_page_load = function (wrapper) {
  new BBDOrderAmendmentPage(wrapper);
};

class BBDOrderAmendmentPage {
  constructor(wrapper) {
    this.page = frappe.ui.make_app_page({
      parent: wrapper,
      title: __("Order Amendment"),
      single_column: true,
    });
    this.wrapper = $(wrapper).find(".layout-main-section");
    this.channels = [];
    this.context = null;
    this.controls = {};
    this.item_controls = [];
    this.proof_attachment = "";
    this.pending_amendment = "";
    this.render_shell();
    this.load_channels();
  }

  render_shell() {
    this.wrapper.html(`
      <div class="bbd-amendment-page">
        <div class="bbd-card bbd-search-card">
          <div class="bbd-card-title">${__("Find WooCommerce Order")}</div>
          <div class="row">
            <div class="col-md-5 bbd-channel"></div>
            <div class="col-md-4 bbd-order-id"></div>
            <div class="col-md-3 bbd-search-action"><button class="btn btn-primary btn-sm">${__("Fetch Order")}</button></div>
          </div>
        </div>
        <div class="bbd-alert-area"></div>
        <div class="bbd-editor"></div>
      </div>
    `);
    this.controls.channel = this.make_control(this.wrapper.find(".bbd-channel"), {
      fieldtype: "Select",
      fieldname: "channel",
      label: __("Channel"),
      reqd: 1,
    });
    this.controls.order_id = this.make_control(this.wrapper.find(".bbd-order-id"), {
      fieldtype: "Data",
      fieldname: "order_id",
      label: __("Woo Order ID"),
      reqd: 1,
    });
    this.wrapper.find(".bbd-search-action button").on("click", () => this.fetch_order());
  }

  make_control(parent, df) {
    const control = frappe.ui.form.make_control({ parent, df, render_input: true });
    control.refresh();
    return control;
  }

  async load_channels() {
    const response = await frappe.call({
      method: "beautybydaz_woocommerce_sync.amendments.get_order_amendment_channels",
      freeze: true,
    });
    this.channels = response.message || [];
    this.controls.channel.df.options = ["", ...this.channels.map((row) => row.name)].join("\n");
    this.controls.channel.refresh();
    if (this.channels.length === 1) this.controls.channel.set_value(this.channels[0].name);
    await this.load_route_options();
  }

  async load_route_options() {
    const options = frappe.route_options || {};
    frappe.route_options = null;
    if (options.amendment) {
      const response = await frappe.call({
        method: "beautybydaz_woocommerce_sync.amendments.get_pending_amendment",
        args: { amendment: options.amendment },
        freeze: true,
      });
      const pending = response.message || {};
      this.pending_amendment = pending.name || "";
      await this.controls.channel.set_value(pending.channel);
      await this.controls.order_id.set_value(pending.order_id);
      await this.fetch_order();
      await this.apply_request(pending.request || {});
      return;
    }
    if (options.channel && options.order_id) {
      await this.controls.channel.set_value(options.channel);
      await this.controls.order_id.set_value(options.order_id);
      await this.fetch_order();
    }
  }

  async apply_request(request) {
    const customer = request.customer || {};
    const values = {
      first_name: customer.first_name,
      last_name: customer.last_name,
      company: customer.company,
      email: customer.email,
      phone: customer.phone,
      status: request.status,
      payment_action: request.payment_action,
      confirmed_payment_amount: request.confirmed_payment_amount,
      shipping_total: request.shipping_total,
      discount_total: request.discount_total,
      reason: request.reason,
    };
    for (const [fieldname, value] of Object.entries(values)) {
      if (value !== undefined && this.controls[fieldname]) {
        await this.controls[fieldname].set_value(value);
      }
    }
    for (const requested of request.items || []) {
      const row = this.item_controls.find((item) => String(item.line_item_id) === String(requested.line_item_id));
      if (!row) continue;
      await row.quantity.set_value(requested.quantity);
      await row.unit_rate.set_value(requested.unit_rate);
    }
    this.proof_attachment = request.proof_attachment || "";
    if (this.proof_attachment) {
      this.wrapper.find(".bbd-proof-status").text(`${__("Proof")}: ${this.proof_attachment}`);
    }
    this.wrapper.find(".bbd-material-confirm").prop("checked", Boolean(request.material_change_confirmed));
    this.wrapper.find(".bbd-fulfilment-confirm").prop("checked", Boolean(request.fulfilment_override_confirmed));
    this.wrapper.find(".bbd-reconciliation-confirm").prop("checked", Boolean(request.reconciliation_review_confirmed));
    this.update_calculated_total();
  }

  show_alert(message, indicator = "orange") {
    this.wrapper.find(".bbd-alert-area").html(
      `<div class="alert alert-${indicator === "red" ? "danger" : indicator === "green" ? "success" : "warning"}">${frappe.utils.escape_html(message)}</div>`
    );
  }

  async fetch_order() {
    const channel = this.controls.channel.get_value();
    const order_id = this.controls.order_id.get_value();
    if (!channel || !order_id) {
      frappe.msgprint(__("Select a channel and enter a Woo order ID."));
      return;
    }
    const response = await frappe.call({
      method: "beautybydaz_woocommerce_sync.amendments.fetch_order_for_amendment",
      args: { channel, order_id },
      freeze: true,
      freeze_message: __("Fetching Woo and ERP records…"),
    });
    this.context = response.message;
    this.context.channel = channel;
    this.render_editor();
  }

  render_editor() {
    const order = this.context.order;
    const erp = this.context.erp_documents;
    const settings = this.context.settings;
    this.item_controls = [];
    this.proof_attachment = "";
    this.wrapper.find(".bbd-alert-area").empty();
    this.wrapper.find(".bbd-editor").html(`
      <div class="bbd-card">
        <div class="bbd-card-title">${__("Order Summary")}</div>
        <div class="bbd-summary-grid">
          <div><span>${__("Woo Order")}</span><strong>#${frappe.utils.escape_html(order.number)}</strong></div>
          <div><span>${__("Current Total")}</span><strong>${format_currency(order.total, order.currency)}</strong></div>
          <div><span>${__("Status")}</span><strong>${frappe.utils.escape_html(order.status)}</strong></div>
          <div><span>${__("Payment")}</span><strong>${frappe.utils.escape_html(order.payment_state)}</strong></div>
          <div><span>${__("Sales Order")}</span><strong>${frappe.utils.escape_html(erp.sales_order.name)}</strong></div>
          <div><span>${__("Invoice")}</span><strong>${frappe.utils.escape_html(erp.sales_invoice.name)}</strong></div>
        </div>
        <div class="bbd-policy-note">
          ${__("Limits")}: ${settings.warning_percent}% ${__("warning")}, ${settings.limit_percent}% ${__("maximum")},
          ${format_currency(settings.max_value_change, order.currency)} ${__("absolute maximum")}.
        </div>
      </div>
      <div class="bbd-card">
        <div class="bbd-card-title">${__("Customer and Order")}</div>
        <div class="row">
          <div class="col-md-4 bbd-first-name"></div><div class="col-md-4 bbd-last-name"></div><div class="col-md-4 bbd-company"></div>
          <div class="col-md-4 bbd-email"></div><div class="col-md-4 bbd-phone"></div>
          <div class="col-md-4 bbd-status"></div><div class="col-md-4 bbd-payment-action"></div><div class="col-md-4 bbd-payment-amount"></div>
        </div>
      </div>
      <div class="bbd-card">
        <div class="bbd-card-title-row"><div class="bbd-card-title">${__("Line Items — quantity and rate changes only")}</div></div>
        <div class="bbd-items"></div>
        <div class="row bbd-totals-row"><div class="col-md-4 bbd-shipping"></div><div class="col-md-4 bbd-discount"></div><div class="col-md-4 bbd-calculated-total"></div></div>
      </div>
      <div class="bbd-card">
        <div class="bbd-card-title">${__("Approval Evidence")}</div>
        <div class="row"><div class="col-md-8 bbd-reason"></div><div class="col-md-4 bbd-proof"></div></div>
        <div class="bbd-checkboxes">
          <label><input type="checkbox" class="bbd-material-confirm"> ${__("I confirm any material price/value change.")}</label>
          <label><input type="checkbox" class="bbd-fulfilment-confirm"> ${__("I confirm the fulfilment override and document-chain rebuild.")}</label>
          <label><input type="checkbox" class="bbd-reconciliation-confirm"> ${__("I reviewed the submitted Payment Entry and approve controlled replacement.")}</label>
        </div>
        <div class="bbd-proof-status text-muted">${__("No proof uploaded")}</div>
        <button class="btn btn-default btn-sm bbd-upload-proof">${__("Upload Proof")}</button>
      </div>
      <div class="bbd-preview-area"></div>
      <div class="bbd-actions"><button class="btn btn-primary btn-sm bbd-preview">${__("Preview Amendment")}</button></div>
    `);
    const customer = order.customer || {};
    this.controls.first_name = this.make_control(this.wrapper.find(".bbd-first-name"), { fieldtype: "Data", label: __("First Name"), fieldname: "first_name", default: customer.first_name });
    this.controls.last_name = this.make_control(this.wrapper.find(".bbd-last-name"), { fieldtype: "Data", label: __("Last Name"), fieldname: "last_name", default: customer.last_name });
    this.controls.company = this.make_control(this.wrapper.find(".bbd-company"), { fieldtype: "Data", label: __("Company"), fieldname: "company", default: customer.company });
    this.controls.email = this.make_control(this.wrapper.find(".bbd-email"), { fieldtype: "Data", options: "Email", label: __("Email"), fieldname: "email", default: customer.email });
    this.controls.phone = this.make_control(this.wrapper.find(".bbd-phone"), { fieldtype: "Data", label: __("Phone"), fieldname: "phone", default: customer.phone });
    this.controls.status = this.make_control(this.wrapper.find(".bbd-status"), { fieldtype: "Select", label: __("Woo Status"), fieldname: "status", options: "processing\non-hold", default: order.status });
    this.controls.payment_action = this.make_control(this.wrapper.find(".bbd-payment-action"), { fieldtype: "Select", label: __("Payment Handling"), fieldname: "payment_action", options: "preserve\nput_on_hold", default: "preserve" });
    const payment = erp.payment_entry ? erp.payment_entry.paid_amount : order.total;
    this.controls.confirmed_payment_amount = this.make_control(this.wrapper.find(".bbd-payment-amount"), { fieldtype: "Currency", label: __("Confirmed Payment Amount"), fieldname: "confirmed_payment_amount", default: payment });
    this.controls.shipping_total = this.make_control(this.wrapper.find(".bbd-shipping"), { fieldtype: "Currency", label: __("Shipping"), fieldname: "shipping_total", default: order.shipping_total, onchange: () => this.update_calculated_total() });
    this.controls.discount_total = this.make_control(this.wrapper.find(".bbd-discount"), { fieldtype: "Currency", label: __("Discount"), fieldname: "discount_total", default: order.discount_total, onchange: () => this.update_calculated_total() });
    this.controls.calculated_total = this.make_control(this.wrapper.find(".bbd-calculated-total"), { fieldtype: "Currency", label: __("Calculated Total Before Tax"), fieldname: "calculated_total", read_only: 1 });
    this.controls.reason = this.make_control(this.wrapper.find(".bbd-reason"), { fieldtype: "Small Text", label: __("Reason"), fieldname: "reason", reqd: 1 });
    (order.items || []).forEach((item) => this.add_item_row(item));

    this.wrapper.find(".bbd-upload-proof").on("click", () => this.upload_proof());
    this.wrapper.find(".bbd-preview").on("click", () => this.preview());
    this.update_calculated_total();
  }

  add_item_row(item) {
    const index = this.item_controls.length;
    const row = $(`
      <div class="bbd-item-row" data-index="${index}">
        <div class="bbd-item-code"></div><div class="bbd-item-qty"></div><div class="bbd-item-rate"></div><div class="bbd-item-tax"></div>

      </div>
    `).appendTo(this.wrapper.find(".bbd-items"));
    const item_code = this.make_control(row.find(".bbd-item-code"), {
      fieldtype: "Link", options: "Item", label: __("Item"), fieldname: `item_code_${index}`, default: item.item_code, read_only: 1,
      get_query: () => ({ query: "beautybydaz_woocommerce_sync.amendments.get_mapped_items", filters: { channel: this.context.channel } }),
    });
    const quantity = this.make_control(row.find(".bbd-item-qty"), { fieldtype: "Float", label: __("Qty"), fieldname: `quantity_${index}`, default: item.quantity, onchange: () => this.update_calculated_total() });
    const unit_rate = this.make_control(row.find(".bbd-item-rate"), { fieldtype: "Currency", label: __("Rate"), fieldname: `unit_rate_${index}`, default: item.unit_rate, onchange: () => this.update_calculated_total() });
    const tax_class = this.make_control(row.find(".bbd-item-tax"), { fieldtype: "Data", label: __("Tax Class"), fieldname: `tax_class_${index}`, default: item.tax_class, read_only: 1 });
    const control = { line_item_id: item.line_item_id || "", item_code, quantity, unit_rate, tax_class, row };
    this.item_controls.push(control);
  }

  update_calculated_total() {
    const subtotal = this.item_controls.reduce((sum, row) => sum + (flt(row.quantity.get_value()) * flt(row.unit_rate.get_value())), 0);
    const total = subtotal + flt(this.controls.shipping_total?.get_value()) - flt(this.controls.discount_total?.get_value());
    if (this.controls.calculated_total) this.controls.calculated_total.set_value(Math.max(total, 0));
  }

  upload_proof() {
    new frappe.ui.FileUploader({
      allow_multiple: false,
      is_private: 1,
      on_success: (file) => {
        this.proof_attachment = file.file_url;
        this.wrapper.find(".bbd-proof-status").text(`${__("Proof")}: ${file.file_name}`);
      },
    });
  }

  payload() {
    return {
      reason: this.controls.reason.get_value(),
      proof_attachment: this.proof_attachment,
      status: this.controls.status.get_value(),
      payment_action: this.controls.payment_action.get_value(),
      confirmed_payment_amount: this.controls.confirmed_payment_amount.get_value(),
      shipping_total: this.controls.shipping_total.get_value(),
      discount_total: this.controls.discount_total.get_value(),
      customer: {
        first_name: this.controls.first_name.get_value(), last_name: this.controls.last_name.get_value(), company: this.controls.company.get_value(),
        email: this.controls.email.get_value(), phone: this.controls.phone.get_value(),
      },
      items: this.item_controls.map((row) => ({
        line_item_id: row.line_item_id,
        item_code: row.item_code.get_value(),
        quantity: row.quantity.get_value(),
        unit_rate: row.unit_rate.get_value(),
        tax_class: row.tax_class.get_value(),
      })),
      material_change_confirmed: this.wrapper.find(".bbd-material-confirm").is(":checked"),
      fulfilment_override_confirmed: this.wrapper.find(".bbd-fulfilment-confirm").is(":checked"),
      reconciliation_review_confirmed: this.wrapper.find(".bbd-reconciliation-confirm").is(":checked"),
    };
  }

  async preview() {
    const response = await frappe.call({
      method: "beautybydaz_woocommerce_sync.amendments.preview_order_amendment",
      args: {
        channel: this.context.channel,
        order_id: this.context.order.id,
        before_hash: this.context.before_hash,
        amendment: JSON.stringify(this.payload()),
      },
      freeze: true,
      freeze_message: __("Validating amendment…"),
    });
    const preview = response.message;
    this.pending_amendment = preview.amendment || this.pending_amendment;
    const warnings = (preview.summary.warnings || []).map((warning) => `<li>${frappe.utils.escape_html(warning)}</li>`).join("");
    this.wrapper.find(".bbd-preview-area").html(`
      <div class="bbd-card bbd-confirm-card">
        <div class="bbd-card-title">${__("Final Confirmation")}</div>
        ${this.pending_amendment ? `<p>${__("Amendment record")}: <strong>${frappe.utils.escape_html(this.pending_amendment)}</strong></p>` : ""}
        <p>${frappe.utils.escape_html(preview.confirmation_message)}</p>
        ${warnings ? `<ul class="text-warning">${warnings}</ul>` : ""}
        <pre>${frappe.utils.escape_html(JSON.stringify(preview.summary.previous_documents, null, 2))}</pre>
        ${this.pending_amendment ? `<button class="btn btn-default btn-sm bbd-open-review">${__("Open Review Record")}</button>` : ""}
        <button class="btn btn-danger btn-sm bbd-apply">${__("Apply Amendment")}</button>
      </div>
    `);
    this.wrapper.find(".bbd-open-review").on("click", () => {
      frappe.set_route("Form", "BBD Order Amendment", this.pending_amendment);
    });
    this.wrapper.find(".bbd-apply").on("click", () => {
      frappe.confirm(
        __("Apply this amendment to WooCommerce and rebuild the linked ERP documents?"),
        () => this.apply(preview.confirmation_token)
      );
    });
  }

  async apply(token) {
    const response = await frappe.call({
      method: "beautybydaz_woocommerce_sync.amendments.apply_order_amendment",
      args: {
        channel: this.context.channel,
        order_id: this.context.order.id,
        before_hash: this.context.before_hash,
        amendment: JSON.stringify(this.payload()),
        confirmation: token,
      },
      freeze: true,
      freeze_message: __("Applying and verifying amendment…"),
    });
    const result = response.message;
    this.show_alert(`${__("Amendment applied")}: ${result.amendment}`, "green");
    this.wrapper.find(".bbd-preview-area").empty();
    await this.fetch_order();
  }
}

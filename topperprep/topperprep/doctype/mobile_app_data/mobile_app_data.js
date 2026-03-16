frappe.ui.form.on('Mobile App Data', {
    refresh: function(frm) {

        if (!frm.is_new()) {

            frm.add_custom_button('Push Data', function() {

                if (!frm.doc.excel_file) {
                    frappe.msgprint("Attach Excel file first");
                    return;
                }

                frappe.call({
                    method: "topperprep.api.api.process_excel",
                    args: {
                        docname: frm.doc.name
                    },
                    callback: function(r) {
                        frappe.msgprint("Data Processed Successfully");
                        frm.reload_doc();
                    }
                });

            });
        }
    }
});

import frappe
import pandas as pd
import json

@frappe.whitelist()
def process_excel(docname):
    doc = frappe.get_doc("Mobile App Data", docname)
    
    file_doc = frappe.get_doc("File", {"file_url": doc.excel_file})
    file_path = file_doc.get_full_path()
    
    df = pd.read_excel(file_path)
    
    # Convert Excel to JSON
    data = df.to_dict(orient="records")
    
    # Save JSON in document field (create Long Text field: json_data)
    # Use json.dumps with ensure_ascii=False to preserve Unicode characters
    doc.db_set("json_data", json.dumps(data, ensure_ascii=False))
    doc.db_set("data_pushed", 1)
    doc.db_set("last_push_date", frappe.utils.now())
    
    return True
@frappe.whitelist(allow_guest=True)
def get_mobile_data(docname):
    doc = frappe.get_doc("Mobile App Data", docname)
    frappe.response['Category'] = doc.category
    
    if not doc.json_data:
        return json.dumps({"message": []}, ensure_ascii=False)
    
    # Parse and return the data directly as JSON string
    data = json.loads(doc.json_data)
    
    return json.dumps(data, ensure_ascii=False)
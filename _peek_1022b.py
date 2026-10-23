import openpyxl
import json

wb = openpyxl.load_workbook(r"C:\Users\ins0002\Downloads\10-22.xlsx", read_only=True, data_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
headers = rows[0]

with open("_peek_1022_out2.txt", "w", encoding="utf-8") as f:
    f.write("ALL HEADERS (%d):\n" % len(headers))
    for i, h in enumerate(headers):
        f.write(f"  [{i}] {h!r}\n")

    f.write("\n--- Sample full rows (as dicts) ---\n")
    for r in rows[1:4]:
        d = dict(zip(headers, r))
        f.write(json.dumps(d, default=str, indent=2) + "\n")

    # Look at SJ/PM Code distinct values
    sj_vals = set()
    status_vals = set()
    repair_class_vals = set()
    last_shift_vals = set()
    for r in rows[1:]:
        d = dict(zip(headers, r))
        sj_vals.add(d.get("SJ / PM Code"))
        status_vals.add(d.get("WO Status"))
        repair_class_vals.add(d.get("Repair Class"))
        last_shift_vals.add(d.get("Last Shift"))
    f.write("\nDistinct SJ / PM Code: " + repr(sj_vals) + "\n")
    f.write("Distinct WO Status: " + repr(status_vals) + "\n")
    f.write("Distinct Repair Class: " + repr(repair_class_vals) + "\n")
    f.write("Distinct Last Shift: " + repr(last_shift_vals) + "\n")

    wo_numbers = [dict(zip(headers,r)).get("WO Number") for r in rows[1:]]
    f.write("\ntotal rows: %d, unique WO numbers: %d\n" % (len(wo_numbers), len(set(wo_numbers))))

print("done")

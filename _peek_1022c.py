import openpyxl
import json

wb = openpyxl.load_workbook(r"C:\Users\ins0002\Downloads\10-22.xlsx", read_only=True, data_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
headers = rows[0]

with open("_peek_1022_out3.txt", "w", encoding="utf-8") as f:
    f.write("ALL HEADERS (%d):\n" % len(headers))
    for i, h in enumerate(headers):
        f.write(f"  [{i}] {h!r}\n")

    # Find rows with SJ/PM Code populated
    pm_rows = []
    for r in rows[1:]:
        d = dict(zip(headers, r))
        if d.get("SJ / PM Code"):
            pm_rows.append(d)
    f.write("\n--- PM rows (%d) ---\n" % len(pm_rows))
    for d in pm_rows:
        keys = ["WO Number","SJ / PM Code","Repair Class","WO Status","Service/Repair Description",
                "Technician","Technician Remarks","Cause","Start Date","WO Closed Date/Time",
                "Asset ID","Asset #","Last Shift"]
        f.write(json.dumps({k: d.get(k) for k in keys}, default=str) + "\n")

    wo_counts = {}
    for d in pm_rows:
        wo_counts[d["WO Number"]] = wo_counts.get(d["WO Number"],0)+1
    f.write("\nUnique PM WO numbers: %d -> %s\n" % (len(wo_counts), list(wo_counts.keys())))
    dupes = {k:v for k,v in wo_counts.items() if v>1}
    f.write("Duplicated PM WO numbers: %s\n" % dupes)

    # Check Asset ID populated rows
    asset_id_pop = sum(1 for r in rows[1:] if dict(zip(headers,r)).get("Asset ID"))
    f.write("\nRows with Asset ID populated: %d / %d\n" % (asset_id_pop, len(rows)-1))

    # check cause populated
    cause_pop = sum(1 for r in rows[1:] if dict(zip(headers,r)).get("Cause"))
    f.write("Rows with Cause populated: %d\n" % cause_pop)

print("done")

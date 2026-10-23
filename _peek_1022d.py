import openpyxl, json

wb = openpyxl.load_workbook(r"C:\Users\ins0002\Downloads\10-22.xlsx", read_only=True, data_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
headers = rows[0]
data = [dict(zip(headers, r)) for r in rows[1:]]

with open("_peek_1022_out4.txt", "w", encoding="utf-8") as f:
    planned = [d for d in data if d.get("Repair Class") == "Planned"]
    unplanned = [d for d in data if d.get("Repair Class") == "Unplanned"]
    f.write(f"Planned rows: {len(planned)}  Unplanned rows: {len(unplanned)}\n")

    sj_populated = [d for d in data if d.get("SJ / PM Code")]
    f.write(f"Rows with SJ/PM Code populated: {len(sj_populated)}\n")

    # cross tab: repair class vs sj populated
    both = [d for d in data if d.get("Repair Class")=="Planned" and d.get("SJ / PM Code")]
    f.write(f"Planned AND SJ-populated: {len(both)}\n")
    planned_no_sj = [d for d in data if d.get("Repair Class")=="Planned" and not d.get("SJ / PM Code")]
    f.write(f"Planned but NO SJ code: {len(planned_no_sj)}\n")
    for d in planned_no_sj:
        f.write("  " + json.dumps({k:d.get(k) for k in ["WO Number","Primary RFR","RFR","Work Accomplished","Service/Repair Description"]}, default=str) + "\n")
    sj_no_planned = [d for d in data if d.get("SJ / PM Code") and d.get("Repair Class")!="Planned"]
    f.write(f"SJ-populated but NOT planned: {len(sj_no_planned)}\n")

    f.write("\n--- Primary RFR / RFR / Work Accomplished for PM rows (SJ populated) ---\n")
    for d in sj_populated:
        f.write(json.dumps({k:d.get(k) for k in ["WO Number","Primary RFR","RFR","Work Accomplished","Repair Class","WO Status","WO Closed Date/Time","Start Date","Cause"]}, default=str) + "\n")

    f.write("\nDistinct Primary RFR overall: " + repr(sorted(set(d.get('Primary RFR') for d in data), key=lambda x: str(x))) + "\n")
    f.write("Distinct Work Accomplished overall: " + repr(sorted(set(d.get('Work Accomplished') for d in data), key=lambda x: str(x))) + "\n")

    # Asset ID vs Asset# populated counts
    aid = [d for d in data if d.get("Asset ID")]
    f.write(f"\nRows with Asset ID populated: {len(aid)}\n")
    for d in aid[:5]:
        f.write("  " + json.dumps({k:d.get(k) for k in ["WO Number","Asset ID","Asset #"]}, default=str) + "\n")

print("done")

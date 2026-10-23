import openpyxl

wb = openpyxl.load_workbook(r"C:\Users\ins0002\Downloads\10-22.xlsx", read_only=True, data_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
headers = rows[0]
with open("_peek_1022_out.txt", "w", encoding="utf-8") as f:
    f.write("HEADERS: " + repr(headers) + "\n")
    f.write("num data rows: " + str(len(rows) - 1) + "\n")
    for r in rows[1:8]:
        f.write(repr(dict(zip(headers, r))) + "\n")
print("done")

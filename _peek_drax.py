import openpyxl

path = r"C:\Users\ins0002\OneDrive - Walmart Inc\Drax  Associates.xlsx"
try:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[0]
    with open("_peek_drax.txt", "w", encoding="utf-8") as f:
        f.write("HEADERS: " + repr(headers) + "\n")
        f.write("rows: %d\n" % (len(rows) - 1))
        for r in rows[1:6]:
            f.write(repr(dict(zip(headers, r))) + "\n")
    print("done")
except Exception as e:
    with open("_peek_drax.txt", "w", encoding="utf-8") as f:
        f.write("ERROR: " + repr(e))
    print("err")

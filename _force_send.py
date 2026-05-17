"""One-off: force-send passdown email for the current active shift without closing it."""
import asyncio, sqlite3, json
from pathlib import Path

DB = Path("dbs/ind3.db")

def _conn():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def main():
    con = _conn()

    # ── Shift ──────────────────────────────────────────────────────────────────
    shift = dict(con.execute(
        "SELECT * FROM shifts WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone())
    print(f"[shift] {shift['shift_type']} | {shift['lead_name']} | {shift['date']} | id={shift['id']}")

    shift_id = shift["id"]

    # ── Last saved report (structured JSON) ────────────────────────────────────
    last = con.execute(
        "SELECT report_json FROM reports WHERE shift_id=? ORDER BY id DESC LIMIT 1",
        (shift_id,)
    ).fetchone()

    from formatter import format_email_passdown
    from report_models import ReportData, RewrittenHappening, OosUnit
    import db as _db

    if last and last["report_json"]:
        data = ReportData.model_validate_json(last["report_json"])
        print("[report] using saved report JSON")
    else:
        # Build from raw entries
        entries = [dict(r) for r in con.execute(
            "SELECT * FROM entries WHERE shift_id=?", (shift_id,)
        ).fetchall()]
        oos_rows = [dict(r) for r in con.execute(
            "SELECT unit FROM oos_units WHERE is_active=1"
        ).fetchall()]
        rts_rows = [dict(r) for r in con.execute(
            "SELECT unit FROM rts_events WHERE shift_id=?", (shift_id,)
        ).fetchall()]
        pm_row = con.execute(
            "SELECT COUNT(*) as n FROM entries WHERE shift_id=? AND is_pm=1", (shift_id,)
        ).fetchone()

        data = ReportData(
            date=shift["date"],
            shift_type=shift["shift_type"],
            lead_name=shift["lead_name"],
            dc_number="IND3",
            happenings=[
                RewrittenHappening(
                    title=e.get("unit", ""),
                    paragraph=f"{e.get('issue','')}. {e.get('action','')}. {e.get('result','')}."
                ) for e in entries
            ],
            oos=[OosUnit(unit=r["unit"]) for r in oos_rows],
            rts=[r["unit"] for r in rts_rows],
            pm_count=pm_row["n"] if pm_row else 0,
        )
        print(f"[report] built from {len(entries)} raw entries")

    print(f"[report] happenings={len(data.happenings)} oos={len(data.oos)} rts={len(data.rts)}")

    # ── Format ─────────────────────────────────────────────────────────────────
    body    = format_email_passdown(data)
    subject = f"IND3 Passdown – {shift['shift_type']} Shift – {shift['lead_name']} – {shift['date']}"

    # ── Recipients ─────────────────────────────────────────────────────────────
    settings_row = con.execute(
        "SELECT value FROM settings WHERE key='email_to'"
    ).fetchone()
    to_raw  = settings_row["value"].strip() if settings_row else ""
    to_list = [a.strip() for a in to_raw.split(",") if a.strip()]
    fn_row  = con.execute("SELECT value FROM settings WHERE key='email_from_name'").fetchone()
    from_name = fn_row["value"].strip() if fn_row else "IND3 Shift Passdown"

    if not to_list:
        print("[email] no recipients configured — add them in Admin → Email Settings")
        return

    print(f"[email] sending to {to_list} as '{from_name}'")
    print("-" * 60)
    print(body.encode('ascii', 'replace').decode('ascii'))
    print("-" * 60)

    import outlook_sender
    asyncio.run(outlook_sender.send_email(
        to_list, subject, body,
        from_name=from_name,
        reply_to=to_list[0],
    ))
    print("[email] SENT OK!")

if __name__ == "__main__":
    main()

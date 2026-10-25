"""Read-only V3 acceptance check for a local Cetaris workbook.

Run on the IND3 workshop PC:
    python scripts/v3_acceptance.py "C:\\path\\to\\trial(1).xlsx" --roster-json roster.json

This validates the real parser and ownership engine; it does not modify the
shift database or replace an authenticated UI upload acceptance test.
Roster JSON maps exact Cetaris technician names to home shifts, e.g.
{"Stamps, Ray": "S3", "Ssaku, Isaac": "S4"}.
"""
import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v3_cetaris import parse_excel, select_records
from v3_split import assign, parse_close


def run(path, roster):
    rows = parse_excel(path.read_bytes())
    techs = {r["tech_name"] for r in rows}
    all_dates = set()
    for r in rows:
        for field in ("start_at", "in_service_at", "opened_at", "closed_at"):
            when = parse_close(r.get(field))
            if when:
                all_dates.add(when.date())
                all_dates.add(when.date() - timedelta(days=1))
    by_shift = {}
    claims = defaultdict(list)
    reviews = defaultdict(list)
    pending_closure = defaultdict(list)
    for d in sorted(all_dates):
        for shift in ("S1", "S2", "S3", "S4", "S5"):
            if (shift in ("S1", "S2", "S3")) != (d.weekday() < 4):
                continue
            attributed, pending = assign(rows, techs, shift, d.isoformat(),
                                         roster_shifts=roster)
            if not attributed and not pending:
                continue
            # Count unique WOs rather than duplicated technician labor rows.
            records, summary = select_records(attributed, techs, None, None)
            done = {r["wo_number"] for r in records if r["kind"] in ("pm", "happening")}
            key = f"{shift} {d.isoformat()}"
            closed_wo = {r["wo_number"] for r in attributed if
                         str(r.get("status","")).strip().casefold() == "closed"}
            unclosed_wo = {r["wo_number"] for r in attributed if
                           str(r.get("status","")).strip().casefold() == "completed"
                           and r["wo_number"] not in closed_wo}
            for wo in unclosed_wo:
                pending_closure[wo].append(key)
            by_shift[key] = {
                "completed_wos": len(done),
                "pm_wos": summary.get("unique_pm_wos", 0),
                "happening_wos": summary.get("unique_happening_wos", 0),
                "oos_wos": summary.get("unique_oos_wos", 0),
                "pending": [p["wo_number"] for p in pending],
                "awaiting_cetaris_closure": sorted(unclosed_wo),
            }
            for wo in done:
                claims[wo].append(key)
            for p in pending:
                reviews[p["wo_number"]].append(key)
    duplicates = {wo: owners for wo, owners in claims.items() if len(owners) > 1}
    result = {
        "workbook": path.name, "parsed_rows": len(rows),
        "technicians": len(techs), "shifts": by_shift,
        "duplicate_completed_ownership": duplicates,
        "unresolved_work_orders": dict(reviews),
        "pending_closure_review": dict(pending_closure),
        "examples": {wo: claims.get(wo, []) for wo in
                     ("28025279", "28025085", "28026013", "28026131")},
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--roster-json", type=Path)
    parser.add_argument("--output", type=Path, default=Path("v3_acceptance_results.json"))
    args = parser.parse_args()
    roster = json.loads(args.roster_json.read_text(encoding="utf-8")) if args.roster_json else {}
    result = run(args.file, roster)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["duplicate_completed_ownership"]:
        print("FAIL: a completed WO was credited to multiple shifts", file=sys.stderr)
        return 2
    if result["unresolved_work_orders"]:
        print("REVIEW: ambiguous work orders require MST/roster confirmation", file=sys.stderr)
        return 3
    if result["pending_closure_review"]:
        print("REVIEW: Completed WOs require Add to Passdown or Ignore in the MST UI", file=sys.stderr)
    print("PASS: all officially Closed WOs have unique shift ownership")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

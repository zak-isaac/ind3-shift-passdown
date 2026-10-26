"""V3 Cetaris-only analytics from accepted, non-excluded uploaded work orders.

Do not use legacy entry.priority ('down', 'degraded', 'monitor'): these are
manual V2 classifications and do not occur in the uploaded Cetaris workbook.
Group labor rows by (shift, WO#) before counting WOs. An involved technician
gets one participation per WO, not one per labor row.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import db

CATEGORIES = ("Breakdown", "Process Interruption", "Proactive Repair", "Work Request")
_CATEGORY_LOOKUP = {name.casefold(): name for name in CATEGORIES}


def get_cetaris_analytics() -> dict:
    """Compute transparent, source-supported counts from the current IND3 DB.

    Source of truth:
      * entries.source='cmms' and entries.excluded=0: accepted non-PM WOs
      * v3_entry_meta.rfr_type: Cetaris RFR repair category for each labor row
      * pm_wos: one PM completion per shift+WO as already deduplicated
      * shifts: shift/date labels, including active shifts whose file was loaded

    This is not a downtime, asset availability, event-severity or labor-hours
    measurement. Those are not established by the uploaded WO files.
    """
    with db._conn() as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        has_meta = "v3_entry_meta" in tables
        meta_join = "LEFT JOIN v3_entry_meta m ON m.entry_id=e.id" if has_meta else ""
        rfr_expr = "COALESCE(m.rfr_type,'')" if has_meta else "''"
        source_rows = c.execute(f"""
            SELECT e.shift_id, e.wo_number, e.unit, e.tech_name,
                   {rfr_expr} AS rfr_type,
                   s.date, s.shift_type
            FROM entries e
            JOIN shifts s ON s.id=e.shift_id
            {meta_join}
            WHERE e.source='cmms' AND e.excluded=0
              AND TRIM(e.wo_number)<>''
            ORDER BY s.date, s.shift_type, e.shift_id, e.wo_number, e.id
        """).fetchall()
        pm_rows = c.execute("""
            SELECT p.shift_id,p.wo_number,p.asset_id,s.date,s.shift_type
            FROM pm_wos p JOIN shifts s ON s.id=p.shift_id
            WHERE TRIM(p.wo_number)<>''
            ORDER BY s.date,s.shift_type,p.shift_id,p.wo_number
        """).fetchall()

    work_orders: dict[tuple[int,str],dict] = {}
    for row in source_rows:
        sid, wo = int(row["shift_id"]), str(row["wo_number"]).strip()
        key = (sid,wo)
        record = work_orders.setdefault(key,{
            "shift_id":sid, "wo_number":wo, "asset":str(row["unit"] or "").strip(),
            "category":"Other / Unspecified", "techs":set(),
            "date":str(row["date"]), "shift_type":str(row["shift_type"]),
        })
        if not record["asset"] and row["unit"]:
            record["asset"] = str(row["unit"]).strip()
        kind = _CATEGORY_LOOKUP.get(str(row["rfr_type"] or "").strip().casefold())
        if kind:
            record["category"] = kind
        if row["tech_name"]:
            record["techs"].add(str(row["tech_name"]).strip())

    shift_labels: dict[int,tuple[str,str]] = {}
    shift_non_pm = Counter()
    asset_to_wos: dict[str,set[tuple[int,str]]] = defaultdict(set)
    asset_to_shifts: dict[str,set[int]] = defaultdict(set)
    type_counts = Counter()
    tech_to_wos: dict[str,set[tuple[int,str]]] = defaultdict(set)
    for key, record in work_orders.items():
        sid = record["shift_id"]
        shift_labels[sid] = (record["date"],record["shift_type"])
        shift_non_pm[sid] += 1
        type_counts[record["category"]] += 1
        if record["asset"]:
            asset_to_wos[record["asset"]].add(key)
            asset_to_shifts[record["asset"]].add(sid)
        for tech in record["techs"]:
            tech_to_wos[tech].add(key)

    shift_pm = Counter()
    pm_keys = set()
    for row in pm_rows:
        sid,wo=int(row["shift_id"]),str(row["wo_number"]).strip()
        if (sid,wo) in pm_keys:
            continue
        pm_keys.add((sid,wo))
        shift_pm[sid] += 1
        shift_labels[sid] = (str(row["date"]),str(row["shift_type"]))

    sorted_shifts=sorted(shift_labels,key=lambda sid:(
        shift_labels[sid][0],shift_labels[sid][1],sid))
    all_trends=[{
        "shift_id":sid, "date":shift_labels[sid][0],
        "shift_type":shift_labels[sid][1],
        "wo_count":shift_non_pm[sid], "pm_count":shift_pm[sid],
    } for sid in sorted_shifts]
    top_assets=sorted((
        {"unit":asset,"cnt":len(keys)} for asset,keys in asset_to_wos.items()
    ),key=lambda x:(-x["cnt"],x["unit"]))[:12]
    repeat_assets=sorted((
        {"unit":asset,"shift_cnt":len(asset_to_shifts[asset]),"total_wo":len(keys)}
        for asset,keys in asset_to_wos.items() if len(keys)>1
    ),key=lambda x:(-x["total_wo"],-x["shift_cnt"],x["unit"]))[:12]
    top_techs=sorted((
        {"tech_name":tech,"cnt":len(keys)}
        for tech,keys in tech_to_wos.items()
    ),key=lambda x:(-x["cnt"],x["tech_name"]))[:12]
    categories=[{"name":kind,"cnt":type_counts[kind]}
                for kind in CATEGORIES if type_counts[kind]]
    if type_counts["Other / Unspecified"]:
        categories.append({"name":"Other / Unspecified","cnt":type_counts["Other / Unspecified"]})
    return {
        "totals":{
            "total_shifts":len(shift_labels),
            "total_entries":len(work_orders),
            "total_pms":len(pm_keys),
            "breakdown_wos":type_counts["Breakdown"],
        },
        "categories":categories,
        "top_assets":top_assets,
        "repeat_assets":repeat_assets,
        "top_techs":top_techs,
        "wo_by_shift":all_trends[-30:],
        "pm_by_shift":all_trends[-30:],
    }

"""Test the WO consolidator with realistic fake data."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import json
from wo_consolidator import consolidate_entries, consolidated_to_happenings

fake_entries = [
    {
        'id': 1, 'shift_id': 1, 'tech_name': 'Isaac S', 'unit': 'M2785',
        'issue': 'WO #27771902 — Inspected conveyor belt drive motor',
        'action': '', 'result': '', 'created_at': '2026-05-17 06:00:00',
        'priority': 'monitor', 'wo_number': '27771902', 'excluded': 0,
        'source': 'cmms',
        'raw_remarks': json.dumps(['Motor was overheating. Checked thermal protection.']),
    },
    {
        'id': 2, 'shift_id': 1, 'tech_name': 'Stanley A', 'unit': 'M2785',
        'issue': 'WO #27771902 — Replaced motor thermal switch',
        'action': '', 'result': '', 'created_at': '2026-05-17 08:30:00',
        'priority': 'high', 'wo_number': '27771902', 'excluded': 0,
        'source': 'cmms',
        'raw_remarks': json.dumps(['Thermal switch replaced. Motor tested and returned to service.']),
    },
    {
        'id': 3, 'shift_id': 1, 'tech_name': 'Isaac S', 'unit': 'M2785',
        'issue': 'WO #27771902 — Final verification',
        'action': '', 'result': '', 'created_at': '2026-05-17 09:00:00',
        'priority': 'monitor', 'wo_number': '27771902', 'excluded': 0,
        'source': 'cmms',
        'raw_remarks': json.dumps(['Unit cleared and verified operational.']),
    },
    {
        'id': 4, 'shift_id': 1, 'tech_name': 'Bob T', 'unit': 'BALER01',
        'issue': 'Baler jam cleared manually', 'action': 'Cleared', 'result': 'RTS',
        'created_at': '2026-05-17 07:00:00',
        'priority': 'monitor', 'wo_number': '', 'excluded': 0,
        'source': 'manual', 'raw_remarks': '[]',
    },
    {
        'id': 5, 'shift_id': 1, 'tech_name': 'Isaac S', 'unit': 'CONVEYOR2',
        'issue': 'WO #27771902 — should be excluded',
        'action': '', 'result': '', 'created_at': '2026-05-17 06:00:00',
        'priority': 'monitor', 'wo_number': '27771902', 'excluded': 1,
        'source': 'cmms',
        'raw_remarks': json.dumps(['Should NOT appear in output']),
    },
]

result = consolidate_entries(fake_entries)

print(f"Consolidated WOs  : {len(result.consolidated)}")
print(f"Passthrough entries: {len(result.passthrough)}")
print()

for wo in result.consolidated:
    print(f"WO #{wo.wo_number}")
    print(f"  Asset      : {wo.asset_id}")
    print(f"  Techs      : {wo.techs}")
    print(f"  Entry count: {wo.entry_count}  (IDs {wo.raw_entry_ids})")
    print(f"  Priority   : {wo.priority}")
    print(f"  Conflicting: {wo.conflicting}")
    print(f"  Remarks    :")
    for r in wo.all_remarks:
        print(f"    - {r}")
    print(f"  Summary    : {wo.summary}")
    print()

happenings = consolidated_to_happenings(result)
print(f"Total report happenings: {len(happenings)}")
for h in happenings:
    wo_tag = h['wo_number'] or 'NO-WO'
    print(f"  [{wo_tag}] {h['title']}: {h['paragraph'][:100]}")

print()
print("PASS — excluded entry did NOT appear in output:",
      all("Should NOT" not in r for wo in result.consolidated for r in wo.all_remarks))

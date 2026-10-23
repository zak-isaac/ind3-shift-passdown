"""Regression check: confirm the negation/contrast/asset-stripping rework
of _source_states_return_to_service still produces the same classification
on all 8 ORIGINAL test cases' raw remarks that the previous live 3-run
sweep (_dev_test_5_FULL_report.txt) already confirmed correct. Pure
function, no Ollama call needed.
"""
import ai_json_rewrite as jr

# (asset_id, remark, previously-confirmed-correct expected value)
cases = [
    ("VRC0001", "Recalibrated each floor", None),
    ("FLEX0007",
     "The technicians together replaced a bad brush on motor 4 with a spare motor "
     "and installed a new brush for the motor.", None),
    ("FLEX0004",
     "Replaced lower brushes on motors 9,5,4 Assisted with installing new brushes on the flex",
     None),
    ("BALER0002",
     "tightened bale wire tension then it kept jamming so had to clear the jam, "
     "re-tension, and it kept jamming again -- eventually swapped the wire spool entirely",
     None),
    ("CR0007", "Calibrated both closers. Asset is operational now.", True),
    ("CR0007", "Assisted with the fix.", None),
    ("TSP0004", "thing on left side making noise checked couldnt find issue", None),
    ("BALER0002", "recalibrated teh bailer sesnor after it was throwing flase erors", None),
]

all_ok = True
for asset, text, expected in cases:
    got = jr._source_states_return_to_service(text, asset)
    ok = got == expected
    all_ok &= ok
    print(f"{'OK  ' if ok else 'FAIL'}  got={got!r:6} expected={expected!r:6}  [{asset}] {text!r}")

print()
print("ALL PASS -- no regression" if all_ok else "REGRESSION DETECTED")

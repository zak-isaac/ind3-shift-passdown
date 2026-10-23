"""One-off sanity check for the asset-name deterministic-override fix.
Pure function calls into validate_structured_output, no Ollama needed.
"""
import ai_json_rewrite as jr

# 1. Model invents/changes the asset name -- must be forced back to truth
#    AND flagged as invented (unlike RTS, an asset mismatch always fails).
parsed = {
    "asset_id": "BALER0099",  # wrong -- true asset is BALER0002
    "actions_performed": ["Recalibrated the sensor"],
    "unresolved_issues": [],
    "returned_to_service": None,
    "narrative": "The sensor was recalibrated.",
}
r = jr.validate_structured_output(parsed, "recalibrated the sensor", "BALER0002", "12345678")
print("Case 1: fabricated asset name")
print(f"  result.asset_id={r.asset_id!r} (expected 'BALER0002', forced to truth)")
print(f"  valid={r.valid} (expected False)")
print(f"  invented_facts={r.invented_facts}")
assert r.asset_id == "BALER0002", "asset_id was not forced back to the true value!"
assert r.valid is False, "a fabricated asset should fail validation!"
print("  PASS\n")

# 2. Model correctly leaves asset_id null when source has none.
parsed2 = {
    "asset_id": None,
    "actions_performed": ["Checked the breaker panel"],
    "unresolved_issues": [],
    "returned_to_service": None,
    "narrative": "The breaker panel was checked.",
}
r2 = jr.validate_structured_output(parsed2, "checked the breaker panel", "", "12345678")
print("Case 2: no asset in source")
print(f"  result.asset_id={r2.asset_id!r} (expected None)")
print(f"  valid={r2.valid} (expected True)")
assert r2.asset_id is None, "asset_id should be None when no asset was supplied!"
assert r2.valid is True
print("  PASS\n")

# 3. Model completes/modifies a partial asset name -- must still be rejected.
parsed3 = {
    "asset_id": "BALER0002-A",  # model "completed" the asset name
    "actions_performed": ["Recalibrated the sensor"],
    "unresolved_issues": [],
    "returned_to_service": None,
    "narrative": "The sensor was recalibrated.",
}
r3 = jr.validate_structured_output(parsed3, "recalibrated the sensor", "BALER0002", "12345678")
print("Case 3: model 'completes' a partial asset name")
print(f"  result.asset_id={r3.asset_id!r} (expected 'BALER0002', forced to truth)")
print(f"  valid={r3.valid} (expected False)")
assert r3.asset_id == "BALER0002"
assert r3.valid is False
print("  PASS\n")

print("ALL PASS")

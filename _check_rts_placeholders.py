"""One-off sanity check for the negation/contrast-aware RTS classifier
against the exact [ASSET]-placeholder patterns. Pure function, no Ollama
call -- safe/instant to run repeatedly. Not part of the pytest suite;
kept as a throwaway dev script like the repo's other _check_*.py files.
"""
import ai_json_rewrite as jr

asset = "BALER0002"
cases = [
    (f"{asset} is operating normally.", True, "true"),
    (f"{asset} is not operating normally.", (False, None), "false or null, never true"),
    (f"Unable to return {asset} to service.", False, "false"),
    (f"{asset} was returned to service but faulted again.", False, "false"),
    (f"{asset} ran briefly; the issue remains unresolved.", False, "false"),
    (f"Repair completed on {asset}.", None, "null"),
    (f"{asset} was reset and tested.", None, "null unless status explicit"),
    (f"{asset} is running, but intermittent faults remain.", False, "false or unresolved"),
]

all_ok = True
for text, expected, spec in cases:
    got = jr._source_states_return_to_service(text, asset)
    ok = got in expected if isinstance(expected, tuple) else got == expected
    all_ok &= ok
    print(f"{'OK  ' if ok else 'FAIL'}  got={got!r:6}  spec={spec!r:28}  text={text!r}")

print()
print("ALL PASS" if all_ok else "SOME FAILED")

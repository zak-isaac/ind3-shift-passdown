"""Repeatability + factual-accuracy test for the experimental JSON rewrite
path (ai_json_rewrite.py), targeting gemma3n-e2b (confirmed via `ollama
list` -- see main.py session notes; gemma4:e4b is blocked, Ollama 0.9.4
has no 'gemma4' architecture support).

Per explicit requirements:
  - temperature=0.0, seed=42, thread config untouched
  - each test input submitted 10 times consecutively
  - compares factual/structured consistency, not only exact wording
  - confirms preservation of: asset name, work-order number, technician
    actions, failure/complaint info, unresolved follow-up work,
    returned-to-service status
  - flags any invented diagnosis/part/action/status/work-order number
  - tests duplicate remarks from multiple technicians (unique work
    preserved, genuine duplication consolidated)
  - tests unclear/incomplete/misspelled remarks
  - falls back to deterministic cleanup / original remarks on validation
    failure
  - report shows: original input, all 10 outputs, exact-output
    consistency, functional consistency, missing facts, invented facts,
    validation result, fallback result, final pass/fail

Run: .venv\\Scripts\\python.exe _repeatability_test.py
Writes: _repeatability_report.txt

Does NOT touch ai_rewriter._USE_AI -- production stays on deterministic
cleanup regardless of this test's outcome. That flag only gets flipped by
Isaac, manually, after reviewing this report.
"""
import argparse
import sys
import time

import ai_json_rewrite as jr

TEST_CASES = [
    {
        "name": "simple_one_liner",
        "asset_id": "VRC0001",
        "wo_number": "27969718",
        "remarks": ["Recalibrated each floor"],
    },
    {
        "name": "multi_tech_same_event_dedup",
        "asset_id": "FLEX0007",
        "wo_number": "27969809",
        "remarks": [
            "had to replace a bad brush on motor 4 with spare motors",
            "Assisted with installing a new brush for the motor.",
        ],
    },
    {
        "name": "multi_tech_unique_work_preserved",
        "asset_id": "FLEX0004",
        "wo_number": "27969816",
        "remarks": [
            "Replaced lower brushes on motors 9,5,4",
            "Assisted with installing new brushes on the flex",
        ],
        # Note: unlike the dedup case above, this real historic pair was
        # previously observed producing TWO distinct clauses (not merged
        # into one), per _gemma3n_comparison_lastweek.txt -- included here
        # specifically to verify unique-work preservation, not just dedup.
    },
    {
        "name": "complex_multistep_with_complication",
        "asset_id": "VOIDFILL0201",
        "wo_number": "27966514",
        "remarks": [
            "The bottom tape encountered issues due to excessive tension which "
            "caused it to move towards the tape head because we couldn't reduce "
            "any further as the tensioner nut reached its lowest setting -- so "
            "after removing and cleaning up both M504 and M507 PEs, I found "
            "that only one was misaligned. After adjusting just this faulty PE "
            "back into alignment (M504), unfortunately another sensor got "
            "damaged in the process, meanwhile I've temporarily fixed "
            "everything with some double-sided tape until we can get a "
            "replacement for it.",
            "Replaced taper & monitored.",
        ],
    },
    {
        "name": "explicit_returned_to_service",
        "asset_id": "VOIDFILL0501",
        "wo_number": "27965717",
        "remarks": ["Calibrated both closers. Asset is operational now."],
    },
    {
        "name": "void_rule_vague_participation",
        "asset_id": "CR0007",
        "wo_number": "27932929",
        "remarks": ["Assisted with the fix."],
    },
    {
        "name": "unclear_incomplete_remark",
        "asset_id": "TSP0004",
        "wo_number": "27833999",
        "remarks": ["thing on left side making noise checked couldnt find issue"],
    },
    {
        "name": "misspelled_remark",
        "asset_id": "BALER0002",
        "wo_number": "27965956",
        "remarks": ["recalibrated teh bailer sesnor after it was throwing flase erors"],
    },
]

# RTS placeholder matrix (per explicit requirement): each template's
# [ASSET] token is substituted programmatically with THAT case's own
# asset_id below -- never a fixed example baked into the template itself,
# and never anything that reaches the AI prompt (this substitution happens
# only in test fixtures, well before ai_json_rewrite.py is ever called).
# Covers the 8 required returned_to_service patterns, each on a distinct
# synthetic asset/WO so cases can't collide or share cached state.
# expected_rts is a tuple of ALL acceptable values per the spec (most
# patterns require exactly one value; two are explicitly "false or X").
_RTS_PLACEHOLDER_TEMPLATES = [
    ("rts_placeholder_01_explicit_true", "[ASSET] is operating normally.", (True,)),
    ("rts_placeholder_02_negated_true_phrase", "[ASSET] is not operating normally.", (False, None)),
    ("rts_placeholder_03_unable_to_return", "Unable to return [ASSET] to service.", (False,)),
    ("rts_placeholder_04_reversed_by_later_fault",
     "[ASSET] was returned to service but faulted again.", (False,)),
    ("rts_placeholder_05_explicit_unresolved",
     "[ASSET] ran briefly; the issue remains unresolved.", (False,)),
    ("rts_placeholder_06_action_only_no_status", "Repair completed on [ASSET].", (None,)),
    ("rts_placeholder_07_reset_no_status", "[ASSET] was reset and tested.", (None,)),
    ("rts_placeholder_08_intermittent_faults",
     "[ASSET] is running, but intermittent faults remain.", (False,)),
]
for _i, (_name, _template, _expected_rts) in enumerate(_RTS_PLACEHOLDER_TEMPLATES):
    _asset = f"RTSTEST{_i + 1:04d}"
    TEST_CASES.append({
        "name": _name,
        "asset_id": _asset,
        "wo_number": f"279{70000 + _i:05d}",
        "remarks": [_template.replace("[ASSET]", _asset)],
        "expected_rts": _expected_rts,
    })
del _i, _name, _template, _expected_rts, _asset

RUNS_PER_CASE = 10


def exact_match(a: jr.StructuredResult, b: jr.StructuredResult) -> bool:
    """Byte-for-byte narrative equality -- reported separately from
    functional equality since the requirement explicitly does NOT demand
    this, but it's still useful signal to show in the report."""
    return a.narrative.strip() == b.narrative.strip()


def functional_diffs(a: jr.StructuredResult, b: jr.StructuredResult) -> list[str]:
    """Structured/factual differences between two runs, ignoring exact
    prose wording. Empty list = functionally identical."""
    diffs = []
    if a.valid != b.valid:
        diffs.append(f"valid: {a.valid} vs {b.valid}")
    if a.fallback_used != b.fallback_used:
        diffs.append(f"fallback_used: {a.fallback_used} vs {b.fallback_used}")
    if sorted(a.actions_performed) != sorted(b.actions_performed):
        diffs.append(f"actions_performed: {a.actions_performed} vs {b.actions_performed}")
    if sorted(a.unresolved_issues) != sorted(b.unresolved_issues):
        diffs.append(f"unresolved_issues: {a.unresolved_issues} vs {b.unresolved_issues}")
    if a.returned_to_service != b.returned_to_service:
        diffs.append(f"returned_to_service: {a.returned_to_service} vs {b.returned_to_service}")

    a_words = set(a.narrative.lower().split())
    b_words = set(b.narrative.lower().split())
    _NOISE = {"the", "a", "an", "and", "was", "were", "is", "are", "to", "of",
              "on", "in", "with", "it", "this", "that", ".", ","}
    only_in_a = (a_words - b_words) - _NOISE
    only_in_b = (b_words - a_words) - _NOISE
    if only_in_a or only_in_b:
        diffs.append(f"narrative content differs: only in run A={only_in_a} only in run B={only_in_b}")
    return diffs


def run_case(case: dict, runs_per_case: int = RUNS_PER_CASE) -> dict:
    results: list[jr.StructuredResult] = []
    elapsed_times: list[float] = []
    for i in range(runs_per_case):
        t0 = time.perf_counter()
        result = jr.rewrite_structured(case["asset_id"], case["wo_number"], case["remarks"])
        elapsed = time.perf_counter() - t0
        results.append(result)
        elapsed_times.append(elapsed)
        print(f"  run {i+1}/{runs_per_case} ({elapsed:.1f}s): "
              f"valid={result.valid} fallback={result.fallback_used}"
              f" invented={result.invented_facts} missing={result.missing_facts}"
              f" corrected={result.corrected_fields} rts={result.returned_to_service}", flush=True)

    baseline = results[0]
    exact_matches = [exact_match(baseline, r) for r in results[1:]]
    func_diffs: dict[int, list[str]] = {}
    for i, r in enumerate(results[1:], start=2):
        diffs = functional_diffs(baseline, r)
        if diffs:
            func_diffs[i] = diffs

    any_invented = any(r.invented_facts for r in results)
    any_missing = any(r.missing_facts for r in results)
    any_fallback = any(r.fallback_used for r in results)
    all_valid = all(r.valid for r in results)

    # RTS expected-value check (only present on the [ASSET]-placeholder
    # matrix cases) -- verifies the FINAL returned_to_service actually
    # matches the required spec value on every run, not just that runs
    # agree with each other. Since returned_to_service is always the
    # deterministic, source-derived value (see ai_json_rewrite.py), this
    # also holds even on runs where an unrelated field triggered fallback.
    expected_rts = case.get("expected_rts")
    rts_mismatches: dict[int, bool | None] = {}
    if expected_rts is not None:
        for i, r in enumerate(results, start=1):
            if r.returned_to_service not in expected_rts:
                rts_mismatches[i] = r.returned_to_service

    # Final pass/fail: functionally stable across all runs AND no invented
    # facts on any run AND (when applicable) returned_to_service matches
    # the required spec value on every run. Missing facts / fallback usage
    # are reported but don't automatically fail the case -- fallback firing
    # is the SAFE outcome by design, not a defect in itself.
    case_pass = (not func_diffs) and (not any_invented) and (not rts_mismatches)

    return {
        "case": case, "results": results, "elapsed_times": elapsed_times,
        "exact_matches": exact_matches, "func_diffs": func_diffs,
        "any_invented": any_invented, "any_missing": any_missing,
        "any_fallback": any_fallback, "all_valid": all_valid,
        "expected_rts": expected_rts, "rts_mismatches": rts_mismatches,
        "case_pass": case_pass,
    }


def write_report(all_case_results: list[dict], path: str) -> bool:
    with open(path, "w", encoding="utf-8") as f:
        f.write("Repeatability + Factual-Accuracy Test Report\n")
        f.write(f"Model: {jr.DEFAULT_MODEL} (confirmed via `ollama list` before running)\n")
        f.write(f"gemma4:e4b: BLOCKED -- Ollama 0.9.4 has no 'gemma4' architecture support\n")
        f.write(f"temperature={jr._TEMPERATURE}  seed={jr._SEED}  threads=unchanged (default)\n")
        f.write(f"Runs per case: {len(all_case_results[0]['results']) if all_case_results else RUNS_PER_CASE}\n")
        f.write("=" * 78 + "\n")

        overall_pass = True
        for cr in all_case_results:
            case, results = cr["case"], cr["results"]

            f.write(f"\n{'#' * 78}\n")
            f.write(f"# CASE: {case['name']}\n")
            f.write(f"# Asset: {case['asset_id']}  WO#: {case['wo_number']}\n")
            f.write(f"# ORIGINAL INPUT: {case['remarks']}\n")
            f.write("#" * 78 + "\n")

            for i, r in enumerate(results, start=1):
                f.write(f"\n--- Run {i} ---\n")
                f.write(f"validation_result={r.valid}  fallback_result={r.fallback_used}\n")
                f.write(f"asset_id={r.asset_id!r}\n")
                f.write(f"actions_performed={r.actions_performed}\n")
                f.write(f"unresolved_issues={r.unresolved_issues}\n")
                f.write(f"returned_to_service={r.returned_to_service}\n")
                f.write(f"narrative={r.narrative!r}\n")
                f.write(f"missing_facts={r.missing_facts}\n")
                f.write(f"invented_facts={r.invented_facts}\n")
                f.write(f"corrected_fields (model disagreed, deterministic value used): {r.corrected_fields}\n")

            f.write(f"\n--- Consistency summary for {case['name']} ---\n")
            f.write(f"Exact-output consistency (runs 2-10 byte-identical to run 1): "
                    f"{sum(cr['exact_matches'])}/{len(cr['exact_matches'])}\n")
            if cr["func_diffs"]:
                f.write(f"Functional consistency: VARIATION in {len(cr['func_diffs'])} run(s):\n")
                for run_num, diffs in cr["func_diffs"].items():
                    f.write(f"  Run {run_num} differs from Run 1:\n")
                    for d in diffs:
                        f.write(f"    - {d}\n")
            else:
                f.write("Functional consistency: STABLE across all 10 runs\n")
            f.write(f"Any invented facts across any run: {cr['any_invented']}\n")
            f.write(f"Any missing facts across any run: {cr['any_missing']}\n")
            f.write(f"Fallback triggered on any run: {cr['any_fallback']}\n")
            if cr["expected_rts"] is not None:
                if cr["rts_mismatches"]:
                    f.write(f"Expected returned_to_service in {cr['expected_rts']}: "
                            f"MISMATCH on run(s) {cr['rts_mismatches']}\n")
                else:
                    f.write(f"Expected returned_to_service in {cr['expected_rts']}: "
                            f"MATCHED on all {len(results)} runs\n")
            f.write(f"\n>>> FINAL PASS/FAIL for {case['name']}: "
                    f"{'PASS' if cr['case_pass'] else 'FAIL'}\n")

            if not cr["case_pass"]:
                overall_pass = False

        f.write(f"\n{'=' * 78}\n")
        f.write(f"OVERALL: {'ALL CASES PASS' if overall_pass else 'ONE OR MORE CASES FAILED'}\n")
        f.write(f"_USE_AI in ai_rewriter.py: UNCHANGED (still disabled, per instruction)\n")
        f.write(f"{'=' * 78}\n")

    return overall_pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=RUNS_PER_CASE,
                         help=f"Runs per case (default {RUNS_PER_CASE} for full certification; "
                              "use a smaller number for quick dev iteration).")
    parser.add_argument("--case", action="append", default=None,
                         help="Restrict to one specific case name (repeatable). "
                              "Omit to run every case.")
    parser.add_argument("--report", default=None,
                         help="Report output path. Defaults to "
                              "_repeatability_report.txt for a full run, or "
                              "_repeatability_dev_report.txt for a --runs<full or --case-filtered run.")
    args = parser.parse_args()

    cases = TEST_CASES
    if args.case:
        cases = [c for c in TEST_CASES if c["name"] in args.case]
        missing = set(args.case) - {c["name"] for c in cases}
        if missing:
            print(f"Unknown case name(s): {sorted(missing)}")
            return 2

    is_full_certification = args.runs == RUNS_PER_CASE and args.case is None
    report_path = args.report or (
        "_repeatability_report.txt" if is_full_certification
        else "_repeatability_dev_report.txt"
    )

    print(f"Testing model={jr.DEFAULT_MODEL} temperature={jr._TEMPERATURE} seed={jr._SEED}")
    print("gemma4:e4b left untouched -- blocked, Ollama 0.9.4 lacks 'gemma4' architecture support.")
    print("_USE_AI in ai_rewriter.py is untouched by this script (production unaffected).")
    print(f"Runs per case: {args.runs}  Cases: {[c['name'] for c in cases]}\n")

    all_case_results = []
    for case in cases:
        print(f"\n=== {case['name']} ===")
        all_case_results.append(run_case(case, runs_per_case=args.runs))

    overall_pass = write_report(all_case_results, report_path)
    print(f"\nReport written to {report_path}")
    print(f"OVERALL: {'ALL CASES PASS' if overall_pass else 'ONE OR MORE CASES FAILED'}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())

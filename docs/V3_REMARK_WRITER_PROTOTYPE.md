# Experimental V3 deterministic maintenance writer

This feature branch introduces a rewrite_wo function returning Draft(text, mode, requires_review, reason).

Currently rewrites tightly verified patterns:
1. Same WO with left/right load-wheel replacement and blue-light, braking and plugging measurements.
2. Semantic duplicate of paper-adjustment remarks.

Exact duplicate notes are deduplicated. Unrecognized repairs, ambiguous statuses or technical notes are preserved and flagged for MST review.

No Ollama or local LLM calls. Not yet wired into active report generation. Further testing on real work orders and source traceability are required.

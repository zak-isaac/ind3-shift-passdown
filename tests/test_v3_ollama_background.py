import v3_ollama_background as ai

def test_source_validation_preserves_measurements():
    okay, _ = ai._verify(["Pressure held at 110 psi, CODE304 active."],
                          "Pressure held at 110 psi; CODE304 remains active.")
    assert okay
    okay, reason = ai._verify(["Pressure held at 110 psi."], "Pressure held at 115 psi.")
    assert not okay
    assert "110 psi" in reason

def test_unsupported_rts_and_final_order_rejected():
    okay, reason = ai._verify(["Replacement part requested."], "Replacement part ordered. RTS.")
    assert not okay
    assert reason == "Unverified RTS"

def test_requested_work_not_accepted_as_ordered():
    okay, reason = ai._verify(["Replacement part requested."], "Replacement part ordered.")
    assert not okay
    assert reason == "Potential unsupported outcome"

def test_model_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IND3_OLLAMA_REWRITE", raising=False)
    assert ai.enabled() is False

def test_extra_technical_details_trigger_review():
    okay, reason = ai._verify(["Installed MDR.C0004, braking 6ft."], "Installed MDR.C0004.")
    assert not okay
    assert "6ft" in reason

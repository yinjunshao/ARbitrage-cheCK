import pytest

from polysim.cli import main
from polysim.data import market_from_dict, market_to_dict
from polysim.demo import ladder_markets, run_demo


def test_demo_triggers_every_control(tmp_path):
    lines = []
    res = run_demo(out=lines.append, audit_dir=tmp_path, approver_name="Louis")
    text = "\n".join(lines)
    assert text.startswith("=") and "PAPER DEMO on SYNTHETIC data" in text

    s1 = res["scene1"]
    assert s1.baskets[0].status == "filled"
    assert s1.metrics["realized_pnl"] == pytest.approx(100 - 96.95)  # floor $1 x 100 minus cost incl. fees

    b2 = res["scene2"].baskets[0]
    assert b2.status == "unwound" and b2.leg_risk_cost == pytest.approx(55.99 - 52.0036)

    s3 = res["scene3"]
    assert "drawdown 5.20%" in s3.risk["kill_reason"]
    assert "order 180.00 vs max 150.00" in text and "reduce-only" in text
    assert "refused: the kill switch can only be reset by a named human" in text
    assert not res["scene3_bt"].risk.killed  # reset by Louis

    assert res["tamper_detected_at"] is not None and "chain BROKEN" in text


def test_audit_verify_command_fails_on_a_tampered_log(tmp_path, capsys):
    run_demo(out=lambda _: None, audit_dir=tmp_path)
    assert main(["audit-verify", str(tmp_path / "scene3.jsonl")]) == 0
    assert main(["audit-verify", str(tmp_path / "scene3-tampered.jsonl")]) == 1
    assert "BROKEN" in capsys.readouterr().out


def test_market_fee_fields_survive_save_and_load():
    m = ladder_markets()[0]
    back = market_from_dict(market_to_dict(m))
    assert (back.category, back.fee_rate, back.fee_exponent, back.fee_taker_only) == ("politics", 0.04, 1.0, True)

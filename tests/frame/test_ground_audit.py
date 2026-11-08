from frame.audit import AuditLog
from frame.ground import check_numbers, extract_numbers, verify_span
from frame.schemas import CoreResult


def core():
    return CoreResult(verdict="look_closely", status="WARN", headline="h", kind="equivalent", naive_edge=0.042,
                      avg_edge=0.021, shares=850.0, ann_ret=0.142, core_version="t")


def test_verify_span_exact_and_whitespace():
    src = 'resolve to "Yes" if the Fed\n  cuts rates'
    assert verify_span("the Fed cuts rates", src)
    assert not verify_span("the Fed lowers rates", src)
    assert not verify_span("", src)


def test_extract_numbers():
    assert extract_numbers("net 2.1% on $1,250 over 54 days, -0.5") == ["2.1%", "$1,250", "54", "-0.5"]


def test_grounded_numbers_pass():
    assert check_numbers("Net edge 2.1% (gross 4.2%), max size 850, annualised 14.2%.", core()) == []


def test_invented_number_rejected():
    bad = check_numbers("Net edge 2.1%, which is about $37 profit on 1,000 contracts.", core())
    assert bad == ["$37", "1,000"]


def test_new_precision_rejected():
    # 2.13% is not 0.021 at that precision
    assert check_numbers("Net edge 2.13%", core()) == ["2.13%"]


def test_numbers_from_verified_quote_allowed():
    assert check_numbers("Deadline December 31, 2026", core(), "by December 31, 2026, 11:59 PM ET") == []


def test_audit_chain_verifies_and_detects_tamper(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    for i in range(5):
        log.append("x", {"i": i})
    assert log.verify() == (True, None)
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    lines[2] = lines[2].replace('"i":2', '"i":99')
    (tmp_path / "a.jsonl").write_text("\n".join(lines) + "\n")
    assert log.verify() == (False, 2)

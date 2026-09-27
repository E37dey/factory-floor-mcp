import json

import pytest

from factory_mcp.governance import WITHHELD, AuditLog, Policy, pseudonym


def test_confidential_columns_are_masked_by_default():
    p = Policy()
    row = {"order_id": "WO-1", "customer": "לקוח א׳", "unit_price_ils": 120, "qty": 10}
    out = p.mask_row("work_orders", row)
    assert out == {"order_id": "WO-1", "customer": WITHHELD, "unit_price_ils": WITHHELD, "qty": 10}
    assert Policy(max_level="confidential").mask_row("work_orders", row) == row


def test_people_become_stable_pseudonyms():
    p = Policy()
    a1 = p.mask_row("downtime", {"operator": "דנה כהן"})["operator"]
    a2 = p.mask_row("downtime", {"operator": "דנה כהן"})["operator"]
    b = p.mask_row("downtime", {"operator": "יוסי לוי"})["operator"]
    assert a1 == a2 != b and a1.startswith("עובד-") and "דנה" not in a1
    assert pseudonym("דנה כהן", "salt-1") != pseudonym("דנה כהן", "salt-2")


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("FACTORY_MCP_MAX_LEVEL", "public")
    monkeypatch.setenv("FACTORY_MCP_MAX_ROWS", "25")
    p = Policy.from_env()
    assert p.max_level == "public" and p.max_rows == 25
    assert p.mask_row("inventory", {"sku": "X", "on_hand": 3})["on_hand"] == WITHHELD  # internal > public
    monkeypatch.setenv("FACTORY_MCP_MAX_LEVEL", "secret")
    with pytest.raises(ValueError):
        Policy.from_env()


def test_audit_log_appends_jsonl(tmp_path):
    log = AuditLog(path=tmp_path / "audit" / "calls.jsonl")
    log.write("machine_oee", {"line": "A", "machine_id": None}, 8, 12.34, True)
    log.write("get_work_orders", {}, 0, 1.0, False, "bad input")
    lines = [json.loads(x) for x in (tmp_path / "audit" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lines[0]["tool"] == "machine_oee" and lines[0]["args"] == {"line": "A"} and lines[0]["rows"] == 8
    assert lines[1]["ok"] is False and lines[1]["error"] == "bad input"
    assert len(log.recent) == 2

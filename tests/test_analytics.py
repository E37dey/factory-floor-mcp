from datetime import date

from factory_mcp import analytics as A


def test_oee_standard_definition():
    rows = [{"machine_id": "M1", "planned_min": 100, "run_min": 80, "total_count": 60, "good_count": 57}]
    r = A.oee(rows, {"M1": 60})  # 60 parts x 60 s = 60 min ideal
    assert (r["availability"], r["performance"], r["quality"]) == (0.8, 0.75, 0.95)
    assert r["oee"] == 0.57
    assert r["downtime_min"] == 20 and r["scrap_count"] == 3


def test_oee_performance_is_capped_and_empty_is_safe():
    rows = [{"machine_id": "M1", "planned_min": 10, "run_min": 10, "total_count": 100, "good_count": 100}]
    assert A.oee(rows, {"M1": 60})["performance"] == 1.0
    assert A.oee([], {})["oee"] == 0.0


def test_downtime_pareto_ranks_and_accumulates():
    m = [{"machine_id": "M1", "line": "A"}, {"machine_id": "M2", "line": "B"}]
    d = date(2026, 9, 1)
    ev = [
        {"date": d, "machine_id": "M1", "reason": "x", "category": "c", "planned": "לא", "minutes": 60},
        {"date": d, "machine_id": "M2", "reason": "y", "category": "c", "planned": "לא", "minutes": 30},
        {"date": d, "machine_id": "M1", "reason": "x", "category": "c", "planned": "לא", "minutes": 10},
        {"date": d, "machine_id": "M1", "reason": "pm", "category": "c", "planned": "כן", "minutes": 500},
    ]
    out = A.downtime_pareto(ev, m, d, d)
    assert [r["reason"] for r in out["reasons"]] == ["x", "y"]  # planned maintenance excluded
    assert out["reasons"][0]["minutes"] == 70 and out["reasons"][0]["events"] == 2
    assert out["reasons"][-1]["cumulative_share"] == 1.0
    assert out["vital_few"] == ["x", "y"]
    assert A.downtime_pareto(ev, m, d, d, line="B")["reasons"][0]["reason"] == "y"
    assert A.downtime_pareto(ev, m, d, d, include_planned=True)["reasons"][0]["reason"] == "pm"


def test_late_and_at_risk_orders():
    as_of = date(2026, 9, 10)
    orders = [
        {"order_id": "1", "status": "בייצור", "due_date": date(2026, 9, 1)},
        {"order_id": "2", "status": "הושלם", "due_date": date(2026, 9, 1)},
        {"order_id": "3", "status": "ממתין לחומר", "due_date": date(2026, 9, 5)},
        {"order_id": "4", "status": "פתוח", "due_date": date(2026, 9, 12)},
        {"order_id": "5", "status": "בייצור", "due_date": date(2026, 9, 12)},
    ]
    late = A.late_orders(orders, as_of)
    assert [o["order_id"] for o in late] == ["1", "3"] and late[0]["days_late"] == 9
    assert [o["order_id"] for o in A.orders_at_risk(orders, as_of, 7)] == ["4"]


def test_inventory_alerts_flag_stockout_before_delivery():
    items = [
        {"sku": "A", "on_hand": 50, "reorder_point": 100, "avg_daily_usage": 10, "lead_time_days": 7},  # 5 days < 7
        {"sku": "B", "on_hand": 90, "reorder_point": 100, "avg_daily_usage": 10, "lead_time_days": 3},  # 9 days
        {"sku": "C", "on_hand": 500, "reorder_point": 100, "avg_daily_usage": 10, "lead_time_days": 3},  # fine
    ]
    out = A.inventory_alerts(items)
    assert [i["sku"] for i in out] == ["A", "B"]
    assert out[0]["stockout_before_delivery"] is True and out[1]["stockout_before_delivery"] is False
    assert out[0]["days_of_cover"] == 5.0


def test_sample_data_tells_a_consistent_story(source):
    prod, machines = source.table("production_log"), source.table("machines")
    s, e = A.default_window(prod, None, None)
    assert (e - s).days == 29
    groups = A.oee_grouped(prod, machines, s, e)
    assert [g["oee"] for g in groups] == sorted(g["oee"] for g in groups)  # worst first
    for g in groups:
        assert abs(g["oee"] - g["availability"] * g["performance"] * g["quality"]) < 0.002
    # CNC-05 has the chronic spindle problem built into the sample
    spindle = {m["machine_id"]: A.downtime_pareto(source.table("downtime"), machines, s, e, machine_id=m["machine_id"])
               for m in machines}
    minutes = {k: next((r["minutes"] for r in v["reasons"] if r["reason"] == "תקלת ציר"), 0) for k, v in spindle.items()}
    assert max(minutes, key=minutes.get) == "CNC-05"


def test_kpis_compare_with_previous_period(source):
    tables = {t: source.table(t) for t in ("machines", "production_log", "downtime", "scrap", "work_orders", "inventory")}
    k = A.kpis(tables, date(2026, 9, 1), date(2026, 9, 14))
    assert k["period"]["days"] == 14 and k["compared_to"] == {"from": "2026-08-18", "to": "2026-08-31"}
    assert k["oee_change_pts"] == round((k["oee"] - k["oee_previous"]) * 100, 1)
    assert 0 < k["oee"] < 1 and k["inventory_below_reorder"] == 3

"""Plant analytics as plain functions over lists of rows.

All numbers the model sees are computed here, in code, so they are exact and
testable. The model's job is to explain them, not to do arithmetic.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from .data import as_date

DONE = "הושלם"


def _r(x: float, n: int = 3) -> float:
    return round(float(x), n)


def in_range(rows: Iterable[dict], key: str, date_from: date | None, date_to: date | None) -> list[dict]:
    out = []
    for r in rows:
        d = as_date(r.get(key))
        if d is None:
            continue
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        out.append(r)
    return out


def last_data_day(prod: list[dict]) -> date:
    days = [as_date(r["date"]) for r in prod if r.get("date")]
    return max(days) if days else date.today()


def default_window(prod: list[dict], date_from: str | None, date_to: str | None, days: int = 30) -> tuple[date, date]:
    end = as_date(date_to) if date_to else last_data_day(prod)
    start = as_date(date_from) if date_from else end - timedelta(days=days - 1)
    if start > end:
        raise ValueError("date_from must be on or before date_to")
    return start, end


# ---------------------------------------------------------------- OEE
def oee(rows: list[dict], cycle: dict[str, float]) -> dict[str, Any]:
    """OEE = availability x performance x quality (the standard definition).

    availability = run time / planned time
    performance  = (ideal cycle time x total count) / run time
    quality      = good count / total count
    """
    planned = sum(r["planned_min"] or 0 for r in rows)
    run = sum(r["run_min"] or 0 for r in rows)
    total = sum(r["total_count"] or 0 for r in rows)
    good = sum(r["good_count"] or 0 for r in rows)
    ideal_min = sum((cycle.get(r["machine_id"], 0) * (r["total_count"] or 0)) / 60 for r in rows)
    a = run / planned if planned else 0.0
    p = min(ideal_min / run, 1.0) if run else 0.0
    q = good / total if total else 0.0
    return {
        "oee": _r(a * p * q),
        "availability": _r(a),
        "performance": _r(p),
        "quality": _r(q),
        "planned_min": int(planned),
        "run_min": int(run),
        "downtime_min": int(planned - run),
        "total_count": int(total),
        "good_count": int(good),
        "scrap_count": int(total - good),
    }


def oee_grouped(prod: list[dict], machines: list[dict], start: date, end: date, group_by: str = "machine",
                machine_id: str | None = None, line: str | None = None) -> list[dict]:
    cycle = {m["machine_id"]: float(m["ideal_cycle_sec"]) for m in machines}
    line_of = {m["machine_id"]: m["line"] for m in machines}
    rows = in_range(prod, "date", start, end)
    if machine_id:
        rows = [r for r in rows if r["machine_id"] == machine_id]
    if line:
        rows = [r for r in rows if line_of.get(r["machine_id"]) == line]
    keyf = {
        "machine": lambda r: r["machine_id"],
        "line": lambda r: line_of.get(r["machine_id"], "?"),
        "day": lambda r: as_date(r["date"]).isoformat(),
        "shift": lambda r: r["shift"],
    }[group_by]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[keyf(r)].append(r)
    out = [{group_by: k, **oee(v, cycle)} for k, v in groups.items()]
    return sorted(out, key=lambda x: x[group_by] if group_by == "day" else x["oee"])


# ---------------------------------------------------------------- downtime
def downtime_pareto(events: list[dict], machines: list[dict], start: date, end: date, machine_id: str | None = None,
                    line: str | None = None, include_planned: bool = False, top: int = 10) -> dict[str, Any]:
    line_of = {m["machine_id"]: m["line"] for m in machines}
    ev = in_range(events, "date", start, end)
    if machine_id:
        ev = [e for e in ev if e["machine_id"] == machine_id]
    if line:
        ev = [e for e in ev if line_of.get(e["machine_id"]) == line]
    if not include_planned:
        ev = [e for e in ev if e.get("planned") != "כן"]
    by: dict[str, dict[str, Any]] = {}
    for e in ev:
        b = by.setdefault(e["reason"], {"reason": e["reason"], "category": e["category"], "minutes": 0, "events": 0, "machines": set()})
        b["minutes"] += int(e["minutes"] or 0)
        b["events"] += 1
        b["machines"].add(e["machine_id"])
    total = sum(b["minutes"] for b in by.values()) or 1
    ranked = sorted(by.values(), key=lambda b: -b["minutes"])
    cum = 0
    out = []
    for b in ranked[:top]:
        cum += b["minutes"]
        out.append({
            "reason": b["reason"], "category": b["category"], "minutes": b["minutes"], "events": b["events"],
            "avg_minutes": _r(b["minutes"] / b["events"], 1), "share": _r(b["minutes"] / total),
            "cumulative_share": _r(cum / total), "machines": sorted(b["machines"]),
        })
    vital = [x["reason"] for x in out if x["cumulative_share"] - x["share"] < 0.8]
    return {"total_minutes": total if ev else 0, "events": len(ev), "reasons": out, "vital_few": vital}


# ---------------------------------------------------------------- orders
def late_orders(orders: list[dict], as_of: date) -> list[dict]:
    out = []
    for o in orders:
        due = as_date(o.get("due_date"))
        if o.get("status") == DONE or due is None or due >= as_of:
            continue
        out.append({**o, "days_late": (as_of - due).days})
    return sorted(out, key=lambda o: -o["days_late"])


def orders_at_risk(orders: list[dict], as_of: date, horizon_days: int = 7) -> list[dict]:
    """Open orders due within the horizon that have not started or wait for material."""
    out = []
    for o in orders:
        due = as_date(o.get("due_date"))
        if due is None or o.get("status") in (DONE, "בבדיקת איכות"):
            continue
        if as_of <= due <= as_of + timedelta(days=horizon_days) and o.get("status") in ("פתוח", "ממתין לחומר"):
            out.append({**o, "days_to_due": (due - as_of).days})
    return sorted(out, key=lambda o: o["days_to_due"])


def on_time_delivery(orders: list[dict], start: date, end: date) -> dict[str, Any]:
    done = [o for o in orders if o.get("status") == DONE and as_date(o.get("completed")) and start <= as_date(o["completed"]) <= end]
    on_time = [o for o in done if as_date(o["completed"]) <= as_date(o["due_date"])]
    return {"completed": len(done), "on_time": len(on_time), "otd": _r(len(on_time) / len(done)) if done else None}


# ---------------------------------------------------------------- inventory
def inventory_alerts(items: list[dict]) -> list[dict]:
    out = []
    for i in items:
        on_hand, rop, use = float(i["on_hand"] or 0), float(i["reorder_point"] or 0), float(i["avg_daily_usage"] or 0)
        cover = on_hand / use if use else None
        lead = int(i.get("lead_time_days") or 0)
        if on_hand > rop:
            continue
        out.append({
            **i,
            "days_of_cover": _r(cover, 1) if cover is not None else None,
            "stockout_before_delivery": bool(cover is not None and cover < lead),
            "suggested_order_qty": max(0, round(rop * 2 - on_hand + use * lead)),
        })
    return sorted(out, key=lambda x: (not x["stockout_before_delivery"], x["days_of_cover"] if x["days_of_cover"] is not None else 1e9))


# ---------------------------------------------------------------- scrap
def scrap_summary(scrap: list[dict], prod: list[dict], start: date, end: date, group_by: str = "reason") -> dict[str, Any]:
    s = in_range(scrap, "date", start, end)
    key = {"reason": "reason", "machine": "machine_id", "product": "product_id"}[group_by]
    total_made = sum(r["total_count"] or 0 for r in in_range(prod, "date", start, end))
    by: dict[str, int] = defaultdict(int)
    for r in s:
        by[r[key]] += int(r["qty"] or 0)
    total = sum(by.values())
    rows = [{group_by: k, "qty": v, "share": _r(v / total) if total else 0} for k, v in sorted(by.items(), key=lambda kv: -kv[1])]
    return {"scrap_qty": total, "produced_qty": total_made, "scrap_rate": _r(total / total_made, 4) if total_made else None, "groups": rows}


# ---------------------------------------------------------------- dashboard
def kpis(tables: dict[str, list[dict]], start: date, end: date) -> dict[str, Any]:
    prod, machines = tables["production_log"], tables["machines"]
    cycle = {m["machine_id"]: float(m["ideal_cycle_sec"]) for m in machines}
    days = (end - start).days + 1
    prev_end, prev_start = start - timedelta(days=1), start - timedelta(days=days)
    cur = oee(in_range(prod, "date", start, end), cycle)
    prev = oee(in_range(prod, "date", prev_start, prev_end), cycle)
    pareto = downtime_pareto(tables["downtime"], machines, start, end, top=3)
    orders = tables["work_orders"]
    as_of = end + timedelta(days=1)
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "compared_to": {"from": prev_start.isoformat(), "to": prev_end.isoformat()},
        "oee": cur["oee"], "oee_previous": prev["oee"], "oee_change_pts": _r((cur["oee"] - prev["oee"]) * 100, 1),
        "availability": cur["availability"], "performance": cur["performance"], "quality": cur["quality"],
        "good_parts": cur["good_count"], "scrap_parts": cur["scrap_count"],
        "unplanned_downtime_hours": _r(pareto["total_minutes"] / 60, 1),
        "top_downtime_reasons": [{"reason": r["reason"], "minutes": r["minutes"]} for r in pareto["reasons"]],
        "on_time_delivery": on_time_delivery(orders, start, end),
        "late_orders_open": len(late_orders(orders, as_of)),
        "inventory_below_reorder": len(inventory_alerts(tables["inventory"])),
    }

"""The MCP server: tools, resources and prompts over the plant data.

Design rules:
* Read-only. No tool changes data in the source systems.
* Numbers are computed in code (analytics.py); the model explains them.
* Every returned row passes the classification policy; every call is audited.
* Row counts are capped, and every list says whether it was truncated.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Callable, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import analytics as A
from .data import DESCRIPTIONS, TABLES, Source, as_date
from .governance import CLASSIFICATION, AuditLog, Policy, level_of

INSTRUCTIONS = """Read-only access to a manufacturing plant's production data: machines, production log,
downtime events, scrap, work orders and inventory. Use the analysis tools (machine_oee, downtime_pareto,
production_kpis, late_work_orders, inventory_alerts, scrap_summary) rather than pulling raw rows and
computing yourself: they return exact numbers. Dates are ISO (YYYY-MM-DD). When no dates are given, tools use
the last 30 days that have data. Data values are in Hebrew; answer the user in the language they use.
Some columns are masked by policy (shown as [מוסתר] or as a pseudonym like עובד-1A2B3C); never try to
re-identify people. If a number looks wrong, say so rather than smoothing it over."""

Date = Annotated[str | None, Field(description="ISO date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")]
Line = Annotated[Literal["A", "B"] | None, Field(description="Production line")]
MachineId = Annotated[str | None, Field(description="Machine id, for example CNC-05", pattern=r"^[A-Za-z0-9_-]{1,20}$")]


def jsonable(x: Any) -> Any:
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    return x


def create_server(source: Source, policy: Policy | None = None, audit: AuditLog | None = None, **kwargs: Any) -> FastMCP:
    policy = policy or Policy()
    audit = audit or AuditLog()
    mcp = FastMCP("factory-floor", instructions=INSTRUCTIONS, **kwargs)

    def tables(*names: str) -> dict[str, list[dict]]:
        return {n: source.table(n) for n in names}

    def cap(table: str, rows: list[dict], limit: int | None) -> dict[str, Any]:
        n = min(limit or policy.max_rows, policy.max_rows)
        return {"rows": jsonable(policy.mask_rows(table, rows[:n])), "returned": min(n, len(rows)),
                "total": len(rows), "truncated": len(rows) > n}

    def audited(name: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        try:
            out = fn()
        except Exception as e:  # logged, then surfaced to the client as a tool error
            audit.write(name, args, 0, (time.perf_counter() - t0) * 1000, False, str(e))
            raise
        rows = out.get("returned", out.get("total", 0)) if isinstance(out, dict) else len(out) if isinstance(out, list) else 0
        audit.write(name, args, int(rows or 0), (time.perf_counter() - t0) * 1000, True)
        return out

    def window(date_from: str | None, date_to: str | None, days: int = 30) -> tuple[date, date]:
        return A.default_window(source.table("production_log"), date_from, date_to, days)

    # ------------------------------------------------------------ discovery
    @mcp.tool(annotations={"readOnlyHint": True, "title": "Datasets"})
    def list_datasets() -> dict[str, Any]:
        """List the available tables with row counts, date coverage and which columns are masked.
        Call this first when you do not know the data."""
        def run():
            out = []
            for t in TABLES:
                rows = source.table(t)
                cols = list(rows[0].keys()) if rows else []
                dates = [as_date(r.get("date")) for r in rows if r.get("date")]
                out.append({"table": t, "description": DESCRIPTIONS[t], "rows": len(rows), "columns": cols,
                            "masked_columns": [c for c in cols if not policy.allows(level_of(t, c))],
                            "from": min(dates).isoformat() if dates else None, "to": max(dates).isoformat() if dates else None})
            return {"source": source.describe(), "policy": policy.describe(), "tables": out, "total": len(out)}
        return audited("list_datasets", {}, run)

    # ------------------------------------------------------------ performance
    @mcp.tool(annotations={"readOnlyHint": True, "title": "OEE"})
    def machine_oee(date_from: Date = None, date_to: Date = None, machine_id: MachineId = None, line: Line = None,
                    group_by: Literal["machine", "line", "day", "shift"] = "machine") -> dict[str, Any]:
        """Overall Equipment Effectiveness (OEE = availability x performance x quality) for a period,
        grouped by machine, line, day or shift. Results by machine are sorted worst first."""
        args = dict(date_from=date_from, date_to=date_to, machine_id=machine_id, line=line, group_by=group_by)
        def run():
            s, e = window(date_from, date_to)
            t = tables("production_log", "machines")
            groups = A.oee_grouped(t["production_log"], t["machines"], s, e, group_by, machine_id, line)
            if machine_id and not groups:
                raise ValueError(f"No production data for machine {machine_id} between {s} and {e}")
            return {"period": {"from": s.isoformat(), "to": e.isoformat()}, "groups": groups, "total": len(groups),
                    "definition": "availability = run/planned; performance = ideal cycle x count / run; quality = good/total"}
        return audited("machine_oee", args, run)

    @mcp.tool(annotations={"readOnlyHint": True, "title": "Downtime Pareto"})
    def downtime_pareto(date_from: Date = None, date_to: Date = None, machine_id: MachineId = None, line: Line = None,
                        include_planned: bool = False, top: Annotated[int, Field(ge=1, le=20)] = 10) -> dict[str, Any]:
        """Downtime by reason, largest first, with share and cumulative share (Pareto). `vital_few` lists the
        reasons that make up the first 80% of lost minutes. Planned maintenance is excluded unless asked."""
        args = dict(date_from=date_from, date_to=date_to, machine_id=machine_id, line=line, include_planned=include_planned, top=top)
        def run():
            s, e = window(date_from, date_to)
            t = tables("downtime", "machines")
            out = A.downtime_pareto(t["downtime"], t["machines"], s, e, machine_id, line, include_planned, top)
            return {"period": {"from": s.isoformat(), "to": e.isoformat()}, **out, "total": len(out["reasons"])}
        return audited("downtime_pareto", args, run)

    @mcp.tool(annotations={"readOnlyHint": True, "title": "Downtime events"})
    def downtime_events(date_from: Date = None, date_to: Date = None, machine_id: MachineId = None,
                        reason: Annotated[str | None, Field(max_length=60)] = None,
                        limit: Annotated[int, Field(ge=1, le=500)] = 50) -> dict[str, Any]:
        """Individual downtime events (newest first) for drill-down after downtime_pareto.
        Operator names are pseudonymized by policy."""
        args = dict(date_from=date_from, date_to=date_to, machine_id=machine_id, reason=reason, limit=limit)
        def run():
            s, e = window(date_from, date_to)
            ev = A.in_range(source.table("downtime"), "date", s, e)
            if machine_id:
                ev = [x for x in ev if x["machine_id"] == machine_id]
            if reason:
                ev = [x for x in ev if reason in str(x["reason"])]
            ev.sort(key=lambda x: (as_date(x["date"]), x["event_id"]), reverse=True)
            return cap("downtime", ev, limit)
        return audited("downtime_events", args, run)

    @mcp.tool(annotations={"readOnlyHint": True, "title": "Scrap"})
    def scrap_summary(date_from: Date = None, date_to: Date = None,
                      group_by: Literal["reason", "machine", "product"] = "reason") -> dict[str, Any]:
        """Scrap quantity and scrap rate for a period, grouped by reason, machine or product."""
        args = dict(date_from=date_from, date_to=date_to, group_by=group_by)
        def run():
            s, e = window(date_from, date_to)
            t = tables("scrap", "production_log")
            out = A.scrap_summary(t["scrap"], t["production_log"], s, e, group_by)
            return {"period": {"from": s.isoformat(), "to": e.isoformat()}, **out, "total": len(out["groups"])}
        return audited("scrap_summary", args, run)

    # ------------------------------------------------------------ orders & stock
    @mcp.tool(annotations={"readOnlyHint": True, "title": "Work orders"})
    def get_work_orders(status: Annotated[Literal["פתוח", "בייצור", "ממתין לחומר", "בבדיקת איכות", "הושלם"] | None,
                                          Field(description="Order status in Hebrew")] = None,
                        line: Line = None, due_from: Date = None, due_to: Date = None,
                        product_id: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_-]{1,30}$")] = None,
                        limit: Annotated[int, Field(ge=1, le=500)] = 50) -> dict[str, Any]:
        """Work orders filtered by status, line, product and due-date range, sorted by due date.
        Customer names and prices are withheld unless the server policy allows confidential data."""
        args = dict(status=status, line=line, due_from=due_from, due_to=due_to, product_id=product_id, limit=limit)
        def run():
            rows = source.table("work_orders")
            if status:
                rows = [r for r in rows if r.get("status") == status]
            if line:
                rows = [r for r in rows if r.get("line") == line]
            if product_id:
                rows = [r for r in rows if r.get("product_id") == product_id]
            rows = A.in_range(rows, "due_date", as_date(due_from), as_date(due_to)) if (due_from or due_to) else rows
            rows.sort(key=lambda r: as_date(r.get("due_date")) or date.max)
            return cap("work_orders", rows, limit)
        return audited("get_work_orders", args, run)

    @mcp.tool(annotations={"readOnlyHint": True, "title": "Late and at-risk orders"})
    def late_work_orders(as_of: Date = None, horizon_days: Annotated[int, Field(ge=1, le=60)] = 7) -> dict[str, Any]:
        """Orders past their due date and not completed (most late first), plus orders due within
        `horizon_days` that have not started or are waiting for material. Default as_of: the day after the
        last production record."""
        args = dict(as_of=as_of, horizon_days=horizon_days)
        def run():
            ref = as_date(as_of) if as_of else A.last_data_day(source.table("production_log")) + timedelta(days=1)
            orders = source.table("work_orders")
            late, risk = A.late_orders(orders, ref), A.orders_at_risk(orders, ref, horizon_days)
            return {"as_of": ref.isoformat(), "late": cap("work_orders", late, None), "at_risk": cap("work_orders", risk, None),
                    "total": len(late) + len(risk)}
        return audited("late_work_orders", args, run)

    @mcp.tool(annotations={"readOnlyHint": True, "title": "Inventory alerts"})
    def inventory_alerts() -> dict[str, Any]:
        """Materials at or below their reorder point, with days of cover, whether they will run out before a new
        delivery can arrive (lead time), and a suggested order quantity. Most urgent first."""
        def run():
            return cap("inventory", A.inventory_alerts(source.table("inventory")), None)
        return audited("inventory_alerts", {}, run)

    # ------------------------------------------------------------ dashboard
    @mcp.tool(annotations={"readOnlyHint": True, "title": "Plant KPIs"})
    def production_kpis(date_from: Date = None, date_to: Date = None) -> dict[str, Any]:
        """Management summary for a period, compared with the previous period of the same length:
        OEE and its three factors, good and scrap parts, unplanned downtime hours with the top reasons,
        on-time delivery, open late orders and materials below reorder point."""
        args = dict(date_from=date_from, date_to=date_to)
        def run():
            s, e = window(date_from, date_to)
            return A.kpis(tables(*TABLES), s, e)
        return audited("production_kpis", args, run)

    @mcp.tool(annotations={"readOnlyHint": True, "title": "Search"})
    def search_records(query: Annotated[str, Field(min_length=2, max_length=60)],
                       table: Annotated[Literal["machines", "downtime", "scrap", "work_orders", "inventory"] | None,
                                        Field(description="Limit the search to one table")] = None,
                       limit: Annotated[int, Field(ge=1, le=100)] = 20) -> dict[str, Any]:
        """Free-text search (substring, case-insensitive) across record fields, for questions like
        'what happened with WO-26017' or 'anything about coolant'. Masked columns are not searched."""
        args = dict(query=query, table=table, limit=limit)
        def run():
            q = query.casefold()
            hits: list[dict] = []
            for t in ([table] if table else ["machines", "work_orders", "inventory", "downtime", "scrap"]):
                for r in source.table(t):
                    visible = {k: v for k, v in r.items() if policy.allows(level_of(t, k))}
                    if any(q in str(v).casefold() for v in visible.values()):
                        hits.append({"table": t, **policy.mask_row(t, r)})
            out = cap("search", hits, limit)
            return out
        return audited("search_records", args, run)

    # ------------------------------------------------------------ resources
    @mcp.resource("factory://schema", name="schema", description="Data dictionary with classification per column", mime_type="application/json")
    def schema() -> str:
        out = {}
        for t in TABLES:
            rows = source.table(t)
            out[t] = {"description": DESCRIPTIONS[t],
                      "columns": {c: level_of(t, c) for c in (rows[0].keys() if rows else CLASSIFICATION.get(t, {}).keys())}}
        return json.dumps(out, ensure_ascii=False, indent=2)

    @mcp.resource("factory://policy", name="policy", description="What this server may return, and what it masks", mime_type="application/json")
    def policy_res() -> str:
        return json.dumps(policy.describe(), ensure_ascii=False, indent=2)

    @mcp.resource("factory://audit/recent", name="audit", description="The last 50 tool calls on this server", mime_type="application/json")
    def audit_res() -> str:
        return json.dumps(list(audit.recent)[-50:], ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ prompts
    @mcp.prompt(name="shift_report", description="Daily production report for the plant manager, in Hebrew")
    def shift_report(day: str = "") -> str:
        d = f"ליום {day}" if day else "ליום הייצור האחרון שיש לו נתונים"
        return (f"הכן דוח ייצור יומי {d} למנהל המפעל, בעברית, עד 200 מילה. "
                "השתמש ב-production_kpis עם date_from ו-date_to זהים ליום הזה, ב-machine_oee לפי מכונה, "
                "ב-downtime_pareto וב-late_work_orders. מבנה: שורה אחת של תמונת מצב; שלוש מכונות עם ה-OEE הנמוך "
                "ומה הסיבה; שלוש סיבות ההשבתה המובילות בדקות; הזמנות באיחור; שלוש פעולות מומלצות למחר, "
                "כל אחת עם בעלים (תפקיד) ומדד. אל תמציא מספרים: כל מספר חייב להגיע מכלי.")

    @mcp.prompt(name="downtime_rca", description="Root-cause analysis for one machine's downtime")
    def downtime_rca(machine_id: str) -> str:
        return (f"בצע ניתוח שורש לבעיות ההשבתה של {machine_id} ב-30 הימים האחרונים. "
                "השתמש ב-downtime_pareto עם machine_id, ב-downtime_events לדוגמאות, וב-machine_oee לפי day כדי לראות מגמה. "
                "השווה למכונות אחרות באותו קו. תן 5 שאלות 'למה' מבוססות נתונים, השערה לסיבת שורש, "
                "ובדיקה פשוטה שתאשר או תפריך אותה. סמן בבירור מה נתון ומה השערה.")

    return mcp

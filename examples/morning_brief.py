"""Call the server like an MCP client would, and print a morning brief.

No language model is involved: this shows exactly what Claude receives from the
tools. Run from the repository root:

    python examples/morning_brief.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "factory_mcp"],
                                   env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()

        async def call(name, **args):
            res = await s.call_tool(name, args)
            return json.loads(res.content[0].text)

        k = await call("production_kpis")
        oee = await call("machine_oee")
        pareto = await call("downtime_pareto", top=3)
        late = await call("late_work_orders")
        stock = await call("inventory_alerts")

        pct = lambda x: f"{x * 100:.1f}%"
        print(f"## תמונת מצב: {k['period']['from']} עד {k['period']['to']}\n")
        print(f"- OEE: **{pct(k['oee'])}** ({k['oee_change_pts']:+} נקודות מול התקופה הקודמת)")
        print(f"- זמינות {pct(k['availability'])} · ביצועים {pct(k['performance'])} · איכות {pct(k['quality'])}")
        print(f"- השבתות לא מתוכננות: {k['unplanned_downtime_hours']} שעות")
        otd = k["on_time_delivery"]
        print(f"- אספקה בזמן: {pct(otd['otd'])} ({otd['on_time']} מתוך {otd['completed']} הזמנות שהושלמו)\n")
        print("### שלוש המכונות עם ה-OEE הנמוך\n")
        for g in oee["groups"][:3]:
            print(f"- {g['machine']}: {pct(g['oee'])} (זמינות {pct(g['availability'])}, ביצועים {pct(g['performance'])}, איכות {pct(g['quality'])})")
        print("\n### סיבות ההשבתה המובילות\n")
        for r in pareto["reasons"]:
            print(f"- {r['reason']}: {r['minutes']:,} דקות, {r['events']} אירועים ({pct(r['share'])})")
        print(f"\n### הזמנות באיחור: {late['late']['total']}\n")
        for o in late["late"]["rows"][:3]:
            print(f"- {o['order_id']} · {o['product_name']} · {o['qty']} יח׳ · {o['days_late']} ימי איחור · {o['status']}")
        print(f"\n### חומרים מתחת לנקודת ההזמנה: {stock['total']}\n")
        for i in stock["rows"]:
            flag = " · **ייגמר לפני שהספק יספיק לספק**" if i["stockout_before_delivery"] else ""
            print(f"- {i['description']}: {i['days_of_cover']} ימי מלאי, זמן אספקה {i['lead_time_days']} ימים{flag}")


if __name__ == "__main__":
    asyncio.run(main())

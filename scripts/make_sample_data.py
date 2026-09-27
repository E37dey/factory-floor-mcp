"""Generate the fictional sample workbook used by the demo and the tests.

The plant is invented: a small precision-machining plant with two lines of CNC
machines. Everything is deterministic (fixed seed), so tests can assert exact
numbers.

    python scripts/make_sample_data.py            # writes data/factory_sample.xlsx
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

SEED = 42
START = date(2026, 7, 1)
END = date(2026, 9, 25)  # last full production day in the sample

MACHINES = [
    # id, name, line, ideal cycle seconds
    ("CNC-01", "מרכז עיבוד DMG 5 צירים", "A", 95),
    ("CNC-02", "מרכז עיבוד DMG 5 צירים", "A", 95),
    ("CNC-03", "מחרטת Okuma", "A", 60),
    ("CNC-04", "מחרטת Okuma", "A", 60),
    ("CNC-05", "משחזת Walter", "B", 140),
    ("CNC-06", "משחזת Walter", "B", 140),
    ("CNC-07", "מרכז עיבוד Mazak", "B", 110),
    ("CNC-08", "מכונת ליטוש", "B", 45),
]
PRODUCTS = [
    ("P-1001", "מקדח קרביד 8 מ״מ"),
    ("P-1002", "מקדח קרביד 12 מ״מ"),
    ("P-2001", "כרסום אצבע 10 מ״מ"),
    ("P-2002", "כרסום כדורי 6 מ״מ"),
    ("P-3001", "תותב הברגה M12"),
    ("P-3002", "ראש חריטה מודולרי"),
]
DOWNTIME_REASONS = [
    # reason, category, planned?, weight, typical minutes
    ("החלפת כלי", "כיוונים והחלפות", False, 30, 18),
    ("המתנה לחומר גלם", "לוגיסטיקה", False, 16, 45),
    ("תקלת ציר", "תקלה מכנית", False, 9, 95),
    ("כיול מדידה", "איכות", False, 12, 25),
    ("חוסר מפעיל", "כוח אדם", False, 8, 60),
    ("תקלת תוכנה/בקר", "תקלה חשמלית", False, 6, 70),
    ("תחזוקה מתוכננת", "תחזוקה", True, 10, 120),
    ("המתנה לאישור איכות", "איכות", False, 9, 35),
]
SCRAP_REASONS = ["סטיית קוטר", "סדק בקרביד", "חספוס פני שטח", "שבר בהשחזה", "סטיית אורך"]
OPERATORS = ["יוסי לוי", "מרים אזולאי", "איגור פטרוב", "סמיר חורי", "דנה כהן", "אבי ביטון", "ולדימיר קוגן"]
MATERIALS = [
    # sku, name, unit, reorder point, daily usage, supplier, unit cost (ILS)
    ("RM-CB-08", "מוט קרביד 8 מ״מ", "יח׳", 400, 55, "ספק קרביד בע״מ", 18),
    ("RM-CB-12", "מוט קרביד 12 מ״מ", "יח׳", 300, 38, "ספק קרביד בע״מ", 35),
    ("RM-CB-10", "מוט קרביד 10 מ״מ", "יח׳", 350, 44, "ספק קרביד בע״מ", 26),
    ("RM-ST-12", "מוט פלדה M12", "יח׳", 250, 30, "מתכות הצפון", 12),
    ("CO-EM-01", "נוזל קירור (ליטר)", "ליטר", 600, 70, "כימיקלים תעשייתיים", 4.5),
    ("TL-GR-01", "אבן השחזה יהלום", "יח׳", 6, 0.6, "כלי חיתוך יבוא", 900),
    ("TL-IN-01", "אינסרט חריטה CNMG", "יח׳", 120, 14, "כלי חיתוך יבוא", 38),
    ("PK-BX-01", "קופסת אריזה ל-10 יח׳", "יח׳", 500, 60, "אריזות הגליל", 1.8),
]


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def is_workday(d: date) -> bool:
    # Israeli work week: Sunday to Thursday (weekday(): Mon=0 ... Sun=6)
    return d.weekday() in (6, 0, 1, 2, 3)


def build(path: Path) -> None:
    rnd = random.Random(SEED)
    wb = Workbook()

    # ---------- machines ----------
    ws = wb.active
    ws.title = "machines"
    ws.append(["machine_id", "name", "line", "ideal_cycle_sec", "installed"])
    for i, (mid, name, line, cyc) in enumerate(MACHINES):
        ws.append([mid, name, line, cyc, date(2015 + i, 1 + i, 10)])

    # ---------- production log + downtime + scrap ----------
    pl = wb.create_sheet("production_log")
    pl.append(["date", "machine_id", "shift", "product_id", "planned_min", "run_min", "total_count", "good_count"])
    dt = wb.create_sheet("downtime")
    dt.append(["event_id", "date", "machine_id", "shift", "reason", "category", "planned", "minutes", "operator"])
    sc = wb.create_sheet("scrap")
    sc.append(["date", "machine_id", "product_id", "reason", "qty"])
    ev = 1
    weights = [r[3] for r in DOWNTIME_REASONS]
    # CNC-05 has a chronic spindle problem, CNC-03 waits for material more than others
    for d in daterange(START, END):
        if not is_workday(d):
            continue
        for mid, _name, _line, cyc in MACHINES:
            for shift in ("בוקר", "ערב"):
                planned = 450
                down = 0
                n_events = rnd.choices([0, 1, 2, 3], weights=[35, 38, 19, 8])[0]
                if mid == "CNC-05" and rnd.random() < 0.18:
                    n_events += 1
                for _ in range(n_events):
                    r = rnd.choices(DOWNTIME_REASONS, weights=weights)[0]
                    if mid == "CNC-05" and rnd.random() < 0.35:
                        r = DOWNTIME_REASONS[2]
                    if mid == "CNC-03" and rnd.random() < 0.3:
                        r = DOWNTIME_REASONS[1]
                    mins = max(5, int(rnd.gauss(r[4], r[4] * 0.35)))
                    mins = min(mins, planned - down - 30)
                    if mins <= 0:
                        continue
                    down += mins
                    dt.append([f"DT-{ev:05d}", d, mid, shift, r[0], r[1], "כן" if r[2] else "לא", mins, rnd.choice(OPERATORS)])
                    ev += 1
                run = planned - down
                perf = rnd.uniform(0.80, 0.95) if mid != "CNC-08" else rnd.uniform(0.70, 0.84)
                total = int(run * 60 / cyc * perf)
                scrap_rate = rnd.uniform(0.005, 0.03) + (0.025 if mid in ("CNC-05", "CNC-06") else 0)
                bad = int(round(total * scrap_rate))
                prod = rnd.choice(PRODUCTS)[0]
                pl.append([d, mid, shift, prod, planned, run, total, total - bad])
                if bad:
                    sc.append([d, mid, prod, rnd.choice(SCRAP_REASONS), bad])

    # ---------- work orders ----------
    wo = wb.create_sheet("work_orders")
    wo.append(["order_id", "customer", "product_id", "product_name", "qty", "line", "status", "created", "due_date", "completed", "unit_price_ils"])
    customers = ["לקוח א׳ (תעשייה ביטחונית)", "לקוח ב׳ (רכב)", "לקוח ג׳ (רפואי)", "לקוח ד׳ (אנרגיה)", "לקוח ה׳ (תעופה)"]
    as_of = date(2026, 9, 27)
    for i in range(1, 121):
        pid, pname = rnd.choice(PRODUCTS)
        created = START + timedelta(days=rnd.randint(0, 84))
        due = created + timedelta(days=rnd.randint(10, 35))
        if due < as_of - timedelta(days=6):
            status = rnd.choices(["הושלם", "בייצור", "ממתין לחומר"], weights=[82, 12, 6])[0]
        elif due < as_of + timedelta(days=5):
            status = rnd.choices(["הושלם", "בייצור", "ממתין לחומר", "בבדיקת איכות"], weights=[40, 35, 12, 13])[0]
        else:
            status = rnd.choices(["פתוח", "בייצור", "ממתין לחומר"], weights=[55, 35, 10])[0]
        completed = None
        if status == "הושלם":
            completed = min(as_of - timedelta(days=1), due + timedelta(days=rnd.randint(-6, 4)))
            if completed < created:
                completed = created + timedelta(days=3)
        line = "A" if pid.startswith(("P-2", "P-3")) else "B"
        wo.append([f"WO-{26000 + i}", rnd.choice(customers), pid, pname, rnd.choice([50, 100, 200, 250, 500, 1000]), line, status, created, due, completed, rnd.choice([38, 52, 64, 85, 120, 190])])

    # ---------- inventory ----------
    inv = wb.create_sheet("inventory")
    inv.append(["sku", "description", "unit", "on_hand", "reorder_point", "avg_daily_usage", "supplier", "lead_time_days", "unit_cost_ils"])
    for sku, name, unit, rop, use, supplier, cost in MATERIALS:
        # a few items deliberately below the reorder point
        factor = rnd.uniform(0.35, 0.9) if sku in ("RM-CB-12", "TL-IN-01", "CO-EM-01") else rnd.uniform(1.1, 2.6)
        inv.append([sku, name, unit, round(rop * factor), rop, use, supplier, rnd.choice([5, 7, 10, 14, 21]), cost])

    # ---------- styling ----------
    for sheet in wb.worksheets:
        for c in sheet[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="0E1726")
        for col in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(col)].width = 18
        sheet.freeze_panes = "A2"
        sheet.sheet_view.rightToLeft = True
        for row in sheet.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, date):
                    c.number_format = "yyyy-mm-dd"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "factory_sample.xlsx"
    build(out)
    print(f"wrote {out}")

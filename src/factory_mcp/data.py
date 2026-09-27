"""Where the data comes from.

A source is anything with ``table(name) -> list[dict]``. The server does not
care whether rows come from an Excel export, SAP, or a database.

* ``ExcelSource`` reads one workbook, one sheet per table, and reloads it when
  the file changes on disk (so a nightly export is picked up without a restart).
* ``RoutedSource`` sends each table to its own source, for example work orders
  from SAP and everything else from Excel.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from openpyxl import load_workbook

TABLES = ("machines", "production_log", "downtime", "scrap", "work_orders", "inventory")

DESCRIPTIONS = {
    "machines": "מכונות: מזהה, שם, קו ייצור וזמן מחזור אידיאלי לחלק (שניות)",
    "production_log": "יומן ייצור לפי מכונה ומשמרת: זמן מתוכנן, זמן ריצה, כמות שיוצרה וכמות תקינה",
    "downtime": "אירועי השבתה: סיבה, קטגוריה, מתוכנן או לא, דקות ומפעיל",
    "scrap": "פסולים: מכונה, מוצר, סיבה וכמות",
    "work_orders": "הזמנות עבודה: לקוח, מוצר, כמות, סטטוס, תאריך יעד ותאריך השלמה",
    "inventory": "מלאי חומרים: כמות, נקודת הזמנה, צריכה יומית ממוצעת, ספק וזמן אספקה",
}


class Source(Protocol):
    def table(self, name: str) -> list[dict[str, Any]]: ...

    def describe(self) -> dict[str, Any]: ...


def _norm(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date() if (v.hour, v.minute, v.second) == (0, 0, 0) else v
    if isinstance(v, str):
        return v.strip()
    return v


class ExcelSource:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Workbook not found: {self.path}")
        self._lock = threading.Lock()
        self._mtime = 0.0
        self._tables: dict[str, list[dict[str, Any]]] = {}

    def _load(self) -> None:
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime and self._tables:
            return
        wb = load_workbook(self.path, read_only=True, data_only=True)
        tables: dict[str, list[dict[str, Any]]] = {}
        for ws in wb.worksheets:
            rows = ws.iter_rows(values_only=True)
            try:
                header = [str(h).strip() for h in next(rows)]
            except StopIteration:
                continue
            tables[ws.title] = [
                {h: _norm(v) for h, v in zip(header, r)} for r in rows if any(c is not None for c in r)
            ]
        wb.close()
        self._tables, self._mtime = tables, mtime

    def table(self, name: str) -> list[dict[str, Any]]:
        if name not in TABLES:
            raise KeyError(f"Unknown table {name!r}. Known tables: {', '.join(TABLES)}")
        with self._lock:
            self._load()
            return list(self._tables.get(name, []))

    def describe(self) -> dict[str, Any]:
        with self._lock:
            self._load()
        return {"type": "excel", "file": self.path.name, "loaded": datetime.fromtimestamp(self._mtime).isoformat(timespec="seconds")}


class RoutedSource:
    """Route individual tables to a different source (for example SAP)."""

    def __init__(self, default: Source, routes: dict[str, Source]):
        self.default, self.routes = default, routes

    def table(self, name: str) -> list[dict[str, Any]]:
        return self.routes.get(name, self.default).table(name)

    def describe(self) -> dict[str, Any]:
        d = {"type": "routed", "default": self.default.describe()}
        d["routes"] = {t: s.describe() for t, s in self.routes.items()}
        return d


def as_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])

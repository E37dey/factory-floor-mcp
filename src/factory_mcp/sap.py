"""Read production orders from SAP S/4HANA over OData (V2).

This adapter reads SAP's Production Order API (API_PRODUCTION_ORDER_2_SRV,
entity A_ProductionOrder_2) and maps it to the ``work_orders`` table the tools
use. The field names follow SAP's public API documentation; systems differ,
so the mapping is a plain dictionary you can adjust (``FIELD_MAP``).

Status: tested against recorded/mocked OData responses only, not against a
live SAP system. Treat it as a starting point for a real integration.

Configuration (environment):
    SAP_ODATA_BASE   e.g. https://my-s4.example.com/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV
    SAP_USER / SAP_PASSWORD   basic auth (a technical user with read-only role), or
    SAP_TOKEN        bearer token (for example from SAP BTP destination service)
    SAP_PLANT        optional plant filter (ProductionPlant)
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx

ENTITY = "A_ProductionOrder_2"

# our column -> SAP field
FIELD_MAP = {
    "order_id": "ManufacturingOrder",
    "product_id": "Material",
    "qty": "MfgOrderPlannedTotalQty",
    "created": "MfgOrderCreationDate",
    "due_date": "MfgOrderPlannedEndDate",
    "completed": "MfgOrderActualEndDate",
    "plant": "ProductionPlant",
}
STATUS_FIELDS = ("OrderIsTechnicallyCompleted", "OrderIsDelivered", "OrderIsConfirmed", "OrderIsReleased")

_ODATA_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d+)?\)/")


def parse_odata_date(v: Any) -> date | None:
    """OData V2 sends dates as '/Date(1695686400000)/'. V4 sends ISO strings."""
    if v in (None, ""):
        return None
    m = _ODATA_DATE.match(str(v))
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date()
    return date.fromisoformat(str(v)[:10])


def map_status(rec: dict[str, Any]) -> str:
    if rec.get("OrderIsTechnicallyCompleted") or rec.get("OrderIsDelivered"):
        return "הושלם"
    if rec.get("OrderIsConfirmed") or rec.get("OrderIsPartiallyConfirmed"):
        return "בייצור"
    if rec.get("OrderIsReleased"):
        return "בייצור"
    return "פתוח"


def map_order(rec: dict[str, Any]) -> dict[str, Any]:
    row = {ours: rec.get(theirs) for ours, theirs in FIELD_MAP.items()}
    for k in ("created", "due_date", "completed"):
        row[k] = parse_odata_date(row[k])
    try:
        row["qty"] = float(row["qty"]) if row["qty"] is not None else None
    except (TypeError, ValueError):
        row["qty"] = None
    row["status"] = map_status(rec)
    row["line"] = rec.get("ProductionLine") or rec.get("WorkCenter")
    row["product_name"] = rec.get("MaterialName") or row["product_id"]
    row["customer"] = None  # not part of the production order; comes from the sales order
    row["unit_price_ils"] = None
    return row


class SapODataSource:
    def __init__(self, base_url: str, *, user: str | None = None, password: str | None = None,
                 token: str | None = None, plant: str | None = None, client: httpx.Client | None = None,
                 page_size: int = 500, max_rows: int = 5000):
        self.base_url = base_url.rstrip("/")
        self.plant = plant
        self.page_size, self.max_rows = page_size, max_rows
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        auth = (user, password) if user and password and not token else None
        self.client = client or httpx.Client(timeout=30, headers=headers, auth=auth)
        if client is not None:
            client.headers.update(headers)

    @classmethod
    def from_env(cls) -> "SapODataSource | None":
        base = os.environ.get("SAP_ODATA_BASE")
        if not base:
            return None
        return cls(base, user=os.environ.get("SAP_USER"), password=os.environ.get("SAP_PASSWORD"),
                   token=os.environ.get("SAP_TOKEN"), plant=os.environ.get("SAP_PLANT"))

    def _pages(self):
        params: dict[str, Any] = {"$format": "json", "$top": self.page_size, "$skip": 0,
                                  "$select": ",".join(list(FIELD_MAP.values()) + list(STATUS_FIELDS))}
        if self.plant:
            params["$filter"] = f"ProductionPlant eq '{self.plant.replace(chr(39), '')}'"
        fetched = 0
        while fetched < self.max_rows:
            r = self.client.get(f"{self.base_url}/{ENTITY}", params=params)
            r.raise_for_status()
            body = r.json()
            results = body.get("d", {}).get("results", body.get("value", []))
            if not results:
                return
            yield results
            fetched += len(results)
            if len(results) < self.page_size:
                return
            params["$skip"] += self.page_size

    def table(self, name: str) -> list[dict[str, Any]]:
        if name != "work_orders":
            raise KeyError(f"SAP adapter only provides work_orders, not {name!r}")
        rows: list[dict[str, Any]] = []
        for page in self._pages():
            rows.extend(map_order(rec) for rec in page)
        return rows

    def describe(self) -> dict[str, Any]:
        return {"type": "sap-odata", "service": self.base_url.rsplit("/", 1)[-1], "entity": ENTITY, "plant": self.plant}

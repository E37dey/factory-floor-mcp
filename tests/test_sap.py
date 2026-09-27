import json
from datetime import date

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from factory_mcp.data import RoutedSource
from factory_mcp.sap import SapODataSource, map_order, parse_odata_date
from factory_mcp.server import create_server

BASE = "https://s4.example.com/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV"


def rec(n, **kw):
    r = {"ManufacturingOrder": f"{1000000 + n}", "Material": "P-1001", "ProductionPlant": "1010",
         "MfgOrderPlannedTotalQty": "250.000", "MfgOrderCreationDate": "/Date(1782864000000)/",
         "MfgOrderPlannedEndDate": "/Date(1785542400000)/", "MfgOrderActualEndDate": None,
         "OrderIsReleased": True, "OrderIsConfirmed": False, "OrderIsTechnicallyCompleted": False, "OrderIsDelivered": False}
    r.update(kw)
    return r


def test_odata_dates_v2_and_v4():
    assert parse_odata_date("/Date(1785542400000)/") == date(2026, 8, 1)
    assert parse_odata_date("2026-08-01T00:00:00Z") == date(2026, 8, 1)
    assert parse_odata_date(None) is None


def test_status_and_field_mapping():
    row = map_order(rec(1))
    assert row["order_id"] == "1000001" and row["qty"] == 250.0 and row["status"] == "בייצור"
    assert row["due_date"] == date(2026, 8, 1)
    assert map_order(rec(2, OrderIsTechnicallyCompleted=True))["status"] == "הושלם"
    assert map_order(rec(3, OrderIsReleased=False))["status"] == "פתוח"


def mock_sap(seen):
    def handler(request: httpx.Request):
        seen.append(request)
        skip = int(request.url.params["$skip"])
        page = [rec(i) for i in range(skip, min(skip + 2, 3))]
        return httpx.Response(200, json={"d": {"results": page}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_paging_filter_and_auth_header():
    seen = []
    src = SapODataSource(BASE, token="t0k", plant="1010", client=mock_sap(seen), page_size=2)
    rows = src.table("work_orders")
    assert len(rows) == 3 and [r.url.params["$skip"] for r in seen] == ["0", "2"]
    assert seen[0].url.params["$filter"] == "ProductionPlant eq '1010'"
    assert seen[0].headers["authorization"] == "Bearer t0k"
    assert seen[0].url.path.endswith("/A_ProductionOrder_2")
    with pytest.raises(KeyError):
        src.table("inventory")


@pytest.mark.anyio
async def test_server_reads_work_orders_from_sap(source):
    sap = SapODataSource(BASE, token="t", client=mock_sap([]), page_size=2)
    srv = create_server(RoutedSource(source, {"work_orders": sap}))
    async with connect(srv) as c:
        r = await c.call_tool("late_work_orders", {"as_of": "2026-09-01"})
        out = json.loads(r.content[0].text)
        assert out["late"]["total"] == 3 and out["late"]["rows"][0]["order_id"].startswith("100000")
        ds = json.loads((await c.call_tool("list_datasets", {})).content[0].text)
        assert ds["source"]["routes"]["work_orders"]["type"] == "sap-odata"

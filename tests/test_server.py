"""Protocol-level tests: a real MCP client session talks to the server in memory."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from factory_mcp.governance import AuditLog, Policy
from factory_mcp.server import create_server

pytestmark = pytest.mark.anyio


def payload(result):
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


async def test_tools_are_listed_and_read_only(source):
    async with connect(create_server(source)) as c:
        tools = (await c.list_tools()).tools
        names = {t.name for t in tools}
        assert {"list_datasets", "machine_oee", "downtime_pareto", "downtime_events", "scrap_summary", "get_work_orders",
                "late_work_orders", "inventory_alerts", "production_kpis", "search_records"} == names
        assert all(t.annotations and t.annotations.readOnlyHint for t in tools)
        oee = next(t for t in tools if t.name == "machine_oee")
        assert set(oee.inputSchema["properties"]) == {"date_from", "date_to", "machine_id", "line", "group_by"}


async def test_kpis_and_oee_through_the_protocol(source):
    async with connect(create_server(source)) as c:
        k = payload(await c.call_tool("production_kpis", {"date_from": "2026-09-01", "date_to": "2026-09-14"}))
        assert k["period"]["days"] == 14 and 0 < k["oee"] < 1
        by_line = payload(await c.call_tool("machine_oee", {"group_by": "line"}))
        assert {g["line"] for g in by_line["groups"]} == {"A", "B"}


async def test_row_cap_and_truncation_flag(source):
    async with connect(create_server(source, Policy(max_rows=5))) as c:
        out = payload(await c.call_tool("get_work_orders", {"limit": 50}))
        assert out["returned"] == 5 and out["total"] == 120 and out["truncated"] is True


async def test_bad_input_is_rejected(source):
    async with connect(create_server(source)) as c:
        r = await c.call_tool("machine_oee", {"date_from": "yesterday"})
        assert r.isError
        r = await c.call_tool("machine_oee", {"machine_id": "CNC-99"})
        assert r.isError and "CNC-99" in r.content[0].text
        r = await c.call_tool("machine_oee", {"date_from": "2026-09-10", "date_to": "2026-09-01"})
        assert r.isError


async def test_masking_applies_to_results_and_search(source):
    async with connect(create_server(source)) as c:
        late = payload(await c.call_tool("late_work_orders", {}))
        assert late["late"]["rows"] and all(r["customer"] == "[מוסתר]" for r in late["late"]["rows"])
        hits = payload(await c.call_tool("search_records", {"query": "תעשייה ביטחונית"}))
        assert hits["total"] == 0  # confidential column is not searchable
    async with connect(create_server(source, Policy(max_level="confidential"))) as c:
        hits = payload(await c.call_tool("search_records", {"query": "תעשייה ביטחונית", "table": "work_orders"}))
        assert hits["total"] > 0


async def test_every_call_is_audited(source):
    audit = AuditLog()
    async with connect(create_server(source, audit=audit)) as c:
        await c.call_tool("inventory_alerts", {})
        await c.call_tool("machine_oee", {"machine_id": "CNC-99"})
    assert [e["tool"] for e in audit.recent] == ["inventory_alerts", "machine_oee"]
    assert audit.recent[0]["ok"] and audit.recent[0]["rows"] == 3
    assert not audit.recent[1]["ok"] and "CNC-99" in audit.recent[1]["error"]


async def test_resources_and_prompts(source):
    async with connect(create_server(source)) as c:
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert {"factory://schema", "factory://policy", "factory://audit/recent"} <= uris
        pol = json.loads((await c.read_resource("factory://policy")).contents[0].text)
        assert pol["read_only"] is True and "customer" in pol["masked_columns"]["work_orders"]
        schema = json.loads((await c.read_resource("factory://schema")).contents[0].text)
        assert schema["downtime"]["columns"]["operator"] == "confidential"
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert prompts == {"shift_report", "downtime_rca"}
        msg = await c.get_prompt("downtime_rca", {"machine_id": "CNC-05"})
        assert "CNC-05" in msg.messages[0].content.text

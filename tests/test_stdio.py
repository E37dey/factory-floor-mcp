"""Start the real server process over stdio, the way Claude Desktop and Claude Code do."""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.anyio
async def test_stdio_process_end_to_end(tmp_path):
    audit = tmp_path / "audit.jsonl"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "factory_mcp"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "FACTORY_MCP_AUDIT_LOG": str(audit)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            info = await s.initialize()
            assert info.serverInfo.name == "factory-floor"
            r = await s.call_tool("downtime_pareto", {"machine_id": "CNC-05", "top": 3})
            out = json.loads(r.content[0].text)
            assert out["reasons"][0]["reason"] == "תקלת ציר"
    assert json.loads(audit.read_text(encoding="utf-8").splitlines()[0])["tool"] == "downtime_pareto"

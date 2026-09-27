"""HTTP transport: bearer token required, OAuth protected-resource metadata published."""

from starlette.testclient import TestClient

from factory_mcp.__main__ import build_http_server
from factory_mcp.governance import AuditLog, Policy

TOKEN = "test-token-0123456789abcdef"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HDR = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def app(source):
    return build_http_server(source, Policy(), AuditLog(), "127.0.0.1", 8000, TOKEN).streamable_http_app()


def test_requests_without_a_valid_token_are_refused(source):
    with TestClient(app(source)) as c:
        assert c.post("/mcp", json=INIT, headers=HDR).status_code == 401
        assert c.post("/mcp", json=INIT, headers={**HDR, "authorization": "Bearer wrong"}).status_code == 401
        r = c.post("/mcp", json=INIT, headers={**HDR, "authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and "factory-floor" in r.text


def test_protected_resource_metadata(source):
    with TestClient(app(source)) as c:
        r = c.get("/.well-known/oauth-protected-resource/mcp")
        if r.status_code == 404:
            r = c.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200 and r.json()["resource"].endswith("/mcp")

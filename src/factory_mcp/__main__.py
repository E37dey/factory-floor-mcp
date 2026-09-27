"""Command line entry point.

    factory-floor-mcp                              # stdio, for Claude Desktop / Claude Code
    factory-floor-mcp --http --port 8000           # Streamable HTTP, requires FACTORY_MCP_TOKEN
    factory-floor-mcp --data /exports/plant.xlsx   # your own workbook
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys
from pathlib import Path

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from .data import ExcelSource, RoutedSource
from .governance import AuditLog, Policy
from .sap import SapODataSource
from .server import create_server

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data" / "factory_sample.xlsx"


class StaticTokenVerifier:
    """Accepts one shared bearer token (constant-time compare).

    Good for an internal deployment behind a reverse proxy. For per-user access,
    replace with a verifier that validates tokens from your identity provider
    (for example Entra ID) and maps groups to scopes.
    """

    def __init__(self, token: str):
        self.token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and hmac.compare_digest(token.encode(), self.token.encode()):
            return AccessToken(token=token, client_id="factory-floor-client", scopes=["factory.read"])
        return None


def build_source(data: str | None):
    excel = ExcelSource(data or os.environ.get("FACTORY_MCP_DATA") or DEFAULT_DATA)
    sap = SapODataSource.from_env()
    return RoutedSource(excel, {"work_orders": sap}) if sap else excel


def build_http_server(source, policy, audit, host: str, port: int, token: str, public_url: str | None = None):
    url = public_url or f"http://{host}:{port}"
    return create_server(
        source, policy, audit,
        host=host, port=port, stateless_http=True,
        token_verifier=StaticTokenVerifier(token),
        auth=AuthSettings(issuer_url=url, resource_server_url=f"{url}/mcp", required_scopes=["factory.read"]),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="factory-floor-mcp", description="Read-only MCP server for plant production data")
    ap.add_argument("--data", help="Excel workbook (default: FACTORY_MCP_DATA or the bundled sample)")
    ap.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    ap.add_argument("--host", default=os.environ.get("FACTORY_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FACTORY_MCP_PORT", "8000")))
    ap.add_argument("--public-url", default=os.environ.get("FACTORY_MCP_PUBLIC_URL"), help="external URL when behind a proxy")
    a = ap.parse_args(argv)

    policy, audit = Policy.from_env(), AuditLog.from_env()
    source = build_source(a.data)
    if not a.http:
        create_server(source, policy, audit).run("stdio")
        return
    token = os.environ.get("FACTORY_MCP_TOKEN", "")
    if len(token) < 24:
        sys.exit("FACTORY_MCP_TOKEN must be set (24+ characters) to serve over HTTP.")
    build_http_server(source, policy, audit, a.host, a.port, token, a.public_url).run("streamable-http")


if __name__ == "__main__":
    main()

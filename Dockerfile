# Read-only MCP server for plant data. Serves Streamable HTTP on port 8000.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    FACTORY_MCP_DATA=/app/data/factory_sample.xlsx \
    FACTORY_MCP_AUDIT_LOG=/var/log/factory-mcp/audit.jsonl \
    FACTORY_MCP_HOST=0.0.0.0 FACTORY_MCP_PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install . \
 && useradd --system --uid 10001 mcp \
 && mkdir -p /var/log/factory-mcp && chown mcp /var/log/factory-mcp

USER mcp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 2)"
CMD ["factory-floor-mcp", "--http"]

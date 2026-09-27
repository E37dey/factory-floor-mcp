"""Data classification, masking and audit logging.

Every column the server can return has a classification level. The server is
configured with the highest level it may return (default: ``internal``).
Columns above that level are masked before anything leaves the server:
people become stable pseudonyms (so "downtime per operator" still works
without names), and money or customer identities are withheld.

Every tool call is written to an append-only JSONL audit log.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEVELS = ("public", "internal", "confidential")

# table -> column -> level. Columns not listed are "internal".
CLASSIFICATION: dict[str, dict[str, str]] = {
    "machines": {"machine_id": "public", "name": "public", "line": "public"},
    "production_log": {},
    "downtime": {"operator": "confidential"},
    "scrap": {},
    "work_orders": {"customer": "confidential", "unit_price_ils": "confidential"},
    "inventory": {"unit_cost_ils": "confidential", "supplier": "internal"},
}

# how a masked value is replaced
PSEUDONYMIZE = {("downtime", "operator")}
WITHHELD = "[מוסתר]"


def level_of(table: str, column: str) -> str:
    return CLASSIFICATION.get(table, {}).get(column, "internal")


def pseudonym(value: str, salt: str) -> str:
    digest = hashlib.sha256((salt + "|" + str(value)).encode("utf-8")).hexdigest()
    return "עובד-" + digest[:6].upper()


@dataclass
class Policy:
    """What the server may return. Configure with environment variables."""

    max_level: str = "internal"
    max_rows: int = 200
    salt: str = "factory-floor-mcp"

    @classmethod
    def from_env(cls) -> "Policy":
        lvl = os.environ.get("FACTORY_MCP_MAX_LEVEL", "internal").strip().lower()
        if lvl not in LEVELS:
            raise ValueError(f"FACTORY_MCP_MAX_LEVEL must be one of {LEVELS}, got {lvl!r}")
        rows = int(os.environ.get("FACTORY_MCP_MAX_ROWS", "200"))
        salt = os.environ.get("FACTORY_MCP_SALT", "factory-floor-mcp")
        return cls(max_level=lvl, max_rows=max(1, min(rows, 5000)), salt=salt)

    def allows(self, level: str) -> bool:
        return LEVELS.index(level) <= LEVELS.index(self.max_level)

    def mask_row(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in row.items():
            lvl = level_of(table, k)
            if self.allows(lvl) or v is None:
                out[k] = v
            elif (table, k) in PSEUDONYMIZE:
                out[k] = pseudonym(v, self.salt)
            else:
                out[k] = WITHHELD
        return out

    def mask_rows(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.mask_row(table, r) for r in rows]

    def describe(self) -> dict[str, Any]:
        masked = {
            t: sorted(c for c, lvl in cols.items() if not self.allows(lvl))
            for t, cols in CLASSIFICATION.items()
        }
        return {
            "max_level": self.max_level,
            "max_rows": self.max_rows,
            "read_only": True,
            "masked_columns": {t: c for t, c in masked.items() if c},
        }


@dataclass
class AuditLog:
    """Append-only JSONL audit log, plus the last entries in memory."""

    path: Path | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=200))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_env(cls) -> "AuditLog":
        p = os.environ.get("FACTORY_MCP_AUDIT_LOG")
        return cls(path=Path(p) if p else None)

    def write(self, tool: str, args: dict[str, Any], rows: int, ms: float, ok: bool, error: str | None = None) -> dict:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "args": {k: v for k, v in args.items() if v is not None},
            "rows": rows,
            "ms": round(ms, 1),
            "ok": ok,
        }
        if error:
            entry["error"] = error[:300]
        with self._lock:
            self.recent.append(entry)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

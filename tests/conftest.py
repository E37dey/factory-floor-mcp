from pathlib import Path

import pytest

from factory_mcp.data import ExcelSource

DATA = Path(__file__).resolve().parents[1] / "data" / "factory_sample.xlsx"


@pytest.fixture(scope="session")
def source():
    return ExcelSource(DATA)


@pytest.fixture
def anyio_backend():
    return "asyncio"

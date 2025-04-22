from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from pytest import FixtureRequest

import stac_fastapi.geoparquet.api
from stac_fastapi.geoparquet import Settings

GEOPARQUET_FILE = Path(__file__).parents[1] / "data" / "naip.parquet"
TOML_FILE = Path(__file__).parents[1] / "data" / "config.toml"


@pytest.fixture(params=[GEOPARQUET_FILE, TOML_FILE])
async def client(request: FixtureRequest) -> AsyncIterator[TestClient]:
    settings = Settings(stac_fastapi_geoparquet_href=str(request.param))
    api = stac_fastapi.geoparquet.api.create(settings)
    with TestClient(api.app) as client:
        yield client

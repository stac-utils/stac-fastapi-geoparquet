from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from pytest import FixtureRequest

import stac_fastapi.geoparquet
from stac_fastapi.geoparquet import Settings

geoparquet_file = Path(__file__).parents[1] / "data" / "naip.parquet"
toml_file = Path(__file__).parents[1] / "data" / "config.toml"


@pytest.fixture(params=[geoparquet_file, toml_file])
async def client(request: FixtureRequest) -> AsyncIterator[TestClient]:
    settings = Settings(stac_fastapi_geoparquet_href=str(request.param))
    api = stac_fastapi.geoparquet.create_api(settings)
    with TestClient(api.app) as client:
        yield client

from fastapi.testclient import TestClient
from pystac import Catalog


async def test_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    d = response.json()
    d["links"] = []  # to stop pystac from resolving links
    catalog = Catalog.from_dict(d)
    catalog.validate()

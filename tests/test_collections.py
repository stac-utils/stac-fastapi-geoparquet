from fastapi.testclient import TestClient
from pystac import Collection


def test_get_collection(client: TestClient) -> None:
    response = client.get("/collections/naip")
    assert response.status_code == 200
    d = response.json()
    d["links"] = []  # stop pystac from resolving links
    Collection.from_dict(d).validate()

    response = client.get("/collections/not-a-collection")
    assert response.status_code == 404

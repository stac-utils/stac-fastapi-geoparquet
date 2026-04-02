from fastapi.testclient import TestClient


def test_get_item(client: TestClient) -> None:
    response = client.get("/collections/naip/items/ne_m_4110264_sw_13_060_20220827")
    assert response.status_code == 200, response.text


def test_items_with_offset(client: TestClient) -> None:
    # https://github.com/stac-utils/stac-fastapi-geoparquet/issues/46
    item_a = (
        client.get("/collections/naip/items?limit=1")
        .raise_for_status()
        .json()["features"][0]
    )
    item_b = (
        client.get("/collections/naip/items?limit=1&offset=1")
        .raise_for_status()
        .json()["features"][0]
    )
    assert item_a["id"] != item_b["id"]

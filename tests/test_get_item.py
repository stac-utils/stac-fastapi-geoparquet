from fastapi.testclient import TestClient


def test_get_search(client: TestClient) -> None:
    response = client.get("/collections/naip/items/ne_m_4110264_sw_13_060_20220827")
    assert response.status_code == 200, response.text

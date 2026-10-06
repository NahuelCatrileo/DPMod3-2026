def test_health_contracto_gateway(client):
    response = client.get("/api//health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data"]["module"] == "module-3"


def test_api_health_se_mantiene_compatible(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_app_boots(app):
    assert app is not None


def test_unauthenticated_request_redirects_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_authenticated_request_returns_200(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200

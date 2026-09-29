def test_landing_is_public_and_shows_version(client):
    resp = client.get("/tanitim")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "LAL Commerce OS" in html
    assert "github.com/siadyalidar/lal-commerce-os-" in html


def test_version_api_is_public(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.get_json()
    assert {"label", "commit", "repo"} <= set(data)


def test_panel_pages_stay_locked(client):
    assert client.get("/finans").status_code == 401

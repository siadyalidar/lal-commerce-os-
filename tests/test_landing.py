def test_landing_page_renders(client):
    resp = client.get("/tanitim")
    assert resp.status_code == 200
    assert "LAL Commerce OS" in resp.get_data(as_text=True)

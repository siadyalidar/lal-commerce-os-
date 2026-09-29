import os


def _creds():
    return os.getenv("PANEL_USERNAME", ""), os.getenv("PANEL_PASSWORD", "")


def test_root_is_public_landing(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "LAL Commerce OS" in html
    assert "github.com/siadyalidar/lal-commerce-os-" in html


def test_old_landing_url_redirects(client):
    assert client.get("/tanitim").status_code == 302


def test_version_api_is_public(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert {"label", "commit", "repo"} <= set(resp.get_json())


def test_login_page_is_public(client):
    assert client.get("/giris").status_code == 200


def test_browser_is_redirected_to_login(client):
    resp = client.get("/panel", headers={"Accept": "text/html"})
    assert resp.status_code == 302
    assert "/giris" in resp.headers["Location"]


def test_api_and_plain_requests_stay_401(client):
    assert client.get("/finans").status_code == 401
    assert client.get("/api/dashboard-summary").status_code == 401


def test_login_flow_and_logout(client):
    user, pw = _creds()
    bad = client.post("/giris", data={"username": user, "password": "x" + pw})
    assert bad.status_code == 401
    ok = client.post("/giris", data={"username": user, "password": pw, "next": "/finans"})
    assert ok.status_code == 302
    assert ok.headers["Location"].endswith("/finans")
    assert client.get("/finans").status_code == 200
    client.get("/cikis")
    assert client.get("/finans").status_code == 401


def test_login_blocks_open_redirect(client):
    user, pw = _creds()
    resp = client.post("/giris", data={"username": user, "password": pw, "next": "//evil.example"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/panel")

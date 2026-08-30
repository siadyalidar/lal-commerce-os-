"""
tests/test_qna_routes.py
----------------------------
Müşteri Soruları paneli sayfası + API uç noktaları için testler.
test_reviews_routes.py ile AYNI desen (auth kontrolü + payload şekli).
"""

from unittest.mock import patch

from tests.conftest import auth_headers


def _seed_question(db, question_id="1001", sku="SH-8IN1-METER", marketplace="trendyol"):
    status = "WAITING_FOR_ANSWER" if marketplace == "trendyol" else "WaitingForAnswer"
    db.upsert_customer_questions([{
        "marketplace": marketplace, "question_id": question_id, "sku": sku,
        "question_text": "Pil ömrü ne kadar?", "status": status,
        "source_created_at": "2026-08-30T10:00:00",
    }])


def test_qna_page_requires_auth(client):
    resp = client.get("/musteri-sorulari")
    assert resp.status_code == 401


def test_qna_page_available_with_auth(client):
    resp = client.get("/musteri-sorulari", headers=auth_headers())
    assert resp.status_code == 200


def test_qna_overview_requires_auth(client):
    resp = client.get("/api/qna/overview")
    assert resp.status_code == 401


def test_qna_overview_shape_when_empty(client, db):
    resp = client.get("/api/qna/overview", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["questions"] == []
    assert data["stats"]["pendingCount"] == 0
    assert data["stats"]["needsClarificationCount"] == 0


def test_qna_overview_returns_seeded_question_with_draft(client, db):
    _seed_question(db)
    db.upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="Cihaz pil ile çalışır.",
                            needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    resp = client.get("/api/qna/overview", headers=auth_headers())
    data = resp.get_json()
    assert len(data["questions"]) == 1
    assert data["questions"][0]["draft_text"] == "Cihaz pil ile çalışır."
    assert data["stats"]["pendingCount"] == 1
    assert data["stats"]["needsClarificationCount"] == 0


def test_qna_overview_counts_needs_clarification_separately(client, db):
    _seed_question(db, question_id="1001")
    db.upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text=None,
                            needs_clarification=True, clarification_prompt="bilgim yok", model_used="gemma4:e4b")
    resp = client.get("/api/qna/overview", headers=auth_headers())
    data = resp.get_json()
    assert data["stats"]["needsClarificationCount"] == 1
    assert data["stats"]["pendingCount"] == 1


def test_qna_resolve_clarification_requires_auth(client):
    resp = client.post("/api/qna/resolve-clarification", json={"sku": "X", "facts": []})
    assert resp.status_code == 401


@patch("blueprints.qna_routes.resolve_clarification")
def test_qna_resolve_clarification_calls_service(mock_resolve, client, db):
    mock_resolve.return_value = {"regenerated": 1, "failed": []}
    resp = client.post("/api/qna/resolve-clarification", headers=auth_headers(), json={
        "sku": "SH-8IN1-METER",
        "topic": "pil",
        "fact_text": "4 adet saat pili kullanılır.",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["regenerated"] == 1
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args.kwargs
    assert call_kwargs["sku"] == "SH-8IN1-METER"
    assert call_kwargs["facts"] == [{"topic": "pil", "fact_text": "4 adet saat pili kullanılır."}]
    assert call_kwargs["created_by"] == "sidar"


@patch("blueprints.qna_routes.resolve_clarification")
def test_qna_resolve_clarification_defaults_topic_to_genel(mock_resolve, client, db):
    mock_resolve.return_value = {"regenerated": 0, "failed": []}
    resp = client.post("/api/qna/resolve-clarification", headers=auth_headers(), json={
        "sku": "SH-8IN1-METER",
        "fact_text": "Garanti süresi 2 yıldır.",
    })
    assert resp.status_code == 200
    call_kwargs = mock_resolve.call_args.kwargs
    assert call_kwargs["facts"][0]["topic"] == "genel"


def test_qna_resolve_clarification_requires_sku_and_fact_text(client, db):
    resp = client.post("/api/qna/resolve-clarification", headers=auth_headers(), json={"sku": "X"})
    assert resp.status_code == 400


def test_qna_finalize_requires_auth(client):
    resp = client.post("/api/qna/finalize", json={"marketplace": "trendyol", "question_id": "1001", "final_text": "x"})
    assert resp.status_code == 401


def test_qna_finalize_marks_sent(client, db):
    _seed_question(db)
    db.upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="ilk taslak",
                            needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    resp = client.post("/api/qna/finalize", headers=auth_headers(), json={
        "marketplace": "trendyol", "question_id": "1001", "final_text": "düzenlenmiş son metin",
    })
    assert resp.status_code == 200
    draft = db.get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft["sent"] is True
    assert draft["draft_text"] == "düzenlenmiş son metin"


def test_qna_finalize_requires_fields(client, db):
    resp = client.post("/api/qna/finalize", headers=auth_headers(), json={"marketplace": "trendyol"})
    assert resp.status_code == 400


def test_qna_overview_no_marketplace_param_returns_all(client, db):
    """30.08.2026 Faz 2: marketplace param verilmezse (ya da 'all') hem
    Trendyol hem HB soruları birlikte dönmeli -- eskiden hardcoded
    marketplace='trendyol' idi, HB verisi hiç görünmüyordu."""
    _seed_question(db, question_id="1001", marketplace="trendyol")
    _seed_question(db, question_id="5001", marketplace="hepsiburada")
    resp = client.get("/api/qna/overview", headers=auth_headers())
    data = resp.get_json()
    assert len(data["questions"]) == 2


def test_qna_overview_filters_by_marketplace_param(client, db):
    _seed_question(db, question_id="1001", marketplace="trendyol")
    _seed_question(db, question_id="5001", marketplace="hepsiburada")
    resp = client.get("/api/qna/overview?marketplace=hepsiburada", headers=auth_headers())
    data = resp.get_json()
    assert len(data["questions"]) == 1
    assert data["questions"][0]["marketplace"] == "hepsiburada"


def test_qna_overview_marketplace_all_returns_both(client, db):
    _seed_question(db, question_id="1001", marketplace="trendyol")
    _seed_question(db, question_id="5001", marketplace="hepsiburada")
    resp = client.get("/api/qna/overview?marketplace=all", headers=auth_headers())
    data = resp.get_json()
    assert len(data["questions"]) == 2

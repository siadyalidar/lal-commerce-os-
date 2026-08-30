"""Müşteri Soruları AI Asistanı paneli -- sayfa + API. reviews_routes.py
ile AYNI desen (Blueprint, database.py'ye ince bir katman, hata yakalama).

Draft-only mimari (29.08.2026 kararı): /api/qna/finalize Trendyol/HB'ye
HİÇBİR ŞEY GÖNDERMEZ -- Sidar taslağı panelde görüp (istersen düzenleyip)
elle Trendyol paneline kopyalar, gönderir, SONRA bu uç nokta yerel kaydı
tutarlı hale getirir (sent=1)."""

from flask import Blueprint, jsonify, render_template, request

import database
from qna_clarification import resolve_clarification

bp = Blueprint("qna_routes", __name__)


@bp.route("/musteri-sorulari")
def qna_page():
    return render_template("pages/musteri-sorulari.html", active_page="musteri-sorulari")


@bp.route("/api/qna/overview")
def qna_overview():
    marketplace = request.args.get("marketplace")
    if marketplace in (None, "", "all", "Tümü"):
        marketplace = None

    try:
        questions = database.list_questions_with_drafts(marketplace=marketplace)
    except Exception as exc:
        return jsonify({"error": f"Sorular yüklenemedi: {exc}"}), 500

    needs_clarification_count = sum(1 for q in questions if q.get("needs_clarification"))

    return jsonify({
        "questions": questions,
        "stats": {
            "pendingCount": len(questions),
            "needsClarificationCount": needs_clarification_count,
        },
    })


@bp.route("/api/qna/resolve-clarification", methods=["POST"])
def qna_resolve_clarification():
    body = request.get_json(silent=True) or {}
    sku = body.get("sku")
    fact_text = body.get("fact_text")
    topic = body.get("topic") or "genel"

    if not sku or not fact_text:
        return jsonify({"error": "sku ve fact_text zorunludur."}), 400

    try:
        result = resolve_clarification(
            sku=sku,
            facts=[{"topic": topic, "fact_text": fact_text}],
            created_by="sidar",
        )
    except Exception as exc:
        return jsonify({"error": f"Netleştirme işlenemedi: {exc}"}), 500

    return jsonify(result)


@bp.route("/api/qna/finalize", methods=["POST"])
def qna_finalize():
    body = request.get_json(silent=True) or {}
    marketplace = body.get("marketplace")
    question_id = body.get("question_id")
    final_text = body.get("final_text")

    if not marketplace or not question_id or not final_text:
        return jsonify({"error": "marketplace, question_id ve final_text zorunludur."}), 400

    try:
        database.finalize_draft_answer(marketplace=marketplace, question_id=question_id, final_text=final_text)
    except Exception as exc:
        return jsonify({"error": f"Kaydedilemedi: {exc}"}), 500

    return jsonify({"ok": True})

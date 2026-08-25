"""Yorumlar ekranı ve review özet/liste API'si (Hepsiburada review sync,
24.08.2026 production'a alındı)."""

from flask import Blueprint, jsonify, render_template

import database

bp = Blueprint("reviews_routes", __name__)


@bp.route("/yorumlar")
def reviews_page():
    return render_template("pages/yorumlar.html", active_page="yorumlar")


@bp.route("/api/reviews/overview")
def reviews_overview():
    """Yorumlar panelinin tek payload'ı: özet istatistik + bugün eklenen
    review'lar (first_synced_at bazlı) + tarihe göre sıralı tam liste.

    Şu an sadece marketplace='hepsiburada' -- Trendyol review sistemi henüz
    yok (bkz. HB_Review_Scraper_Audit_Mimari_Raporu.md Bölüm N: şema
    marketplace-independent tasarlandı, ileride eklenebilir)."""
    try:
        stats = database.get_review_stats("hepsiburada")
        today_reviews = database.list_reviews_today("hepsiburada")
        reviews = database.list_reviews_sorted_by_date("hepsiburada", limit=200)
    except Exception as exc:
        return jsonify({"error": f"Yorumlar yüklenemedi: {exc}"}), 500

    return jsonify({
        "stats": stats,
        "todayReviews": today_reviews,
        "reviews": reviews,
    })

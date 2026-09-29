"""
blueprints/landing_routes.py
--------------------------------
Herkese açık tanıtım (landing) sayfası ve sürüm bilgisi. Panel route'larına dokunmaz.
app.py'de kayıt: app.register_blueprint(landing_routes_bp)
/tanitim ve /api/version, app.py'deki Basic Auth kontrolünden muaftır
(_PUBLIC_PATHS); ikisi de sipariş/finans verisi içermez.
"""

from flask import Blueprint, jsonify, render_template

from version_info import get_version

bp = Blueprint("landing_routes", __name__)


@bp.route("/tanitim")
def landing_page():
    return render_template("landing.html")


@bp.route("/api/version")
def version_api():
    return jsonify(get_version())


@bp.app_context_processor
def _inject_version():
    """Tüm şablonlarda {{ app_version.label }} kullanılabilir."""
    return {"app_version": get_version()}

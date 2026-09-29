"""
blueprints/landing_routes.py
--------------------------------
Herkese acik ana sayfa (/) ve surum bilgisi. Panel artik /panel altinda.
app.py'de kayit: app.register_blueprint(landing_routes_bp)
/, /tanitim, /giris, /cikis ve /api/version app.py'deki _PUBLIC_PATHS ile
kimlik dogrulamasindan muaftir; hicbiri siparis/finans verisi icermez.
"""

from flask import Blueprint, jsonify, redirect, render_template

from version_info import get_version

bp = Blueprint("landing_routes", __name__)


@bp.route("/")
def landing_page():
    return render_template("landing.html")


@bp.route("/tanitim")
def landing_legacy():
    return redirect("/")


@bp.route("/api/version")
def version_api():
    return jsonify(get_version())


@bp.app_context_processor
def _inject_version():
    """Tum sablonlarda {{ app_version.label }} kullanilabilir."""
    return {"app_version": get_version()}

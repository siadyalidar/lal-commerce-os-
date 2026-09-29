"""
blueprints/landing_routes.py
--------------------------------
Herkese acik ana sayfa (/) ve surum bilgisi. Panel artik /panel altinda.
app.py'de kayit: app.register_blueprint(landing_routes_bp)
/, /tanitim, /giris, /cikis ve /api/version app.py'deki _PUBLIC_PATHS ile
kimlik dogrulamasindan muaftir; hicbiri siparis/finans verisi icermez.
"""

import os

from flask import Blueprint, jsonify, redirect, render_template, request, session

from blueprints.auth_routes import _safe_next
from version_info import get_version

bp = Blueprint("landing_routes", __name__)


_HATA = {
    "yanlis": "Kullanıcı adı veya parola hatalı.",
    "kilit": "Çok fazla hatalı deneme. Birkaç dakika sonra tekrar dene.",
}


@bp.route("/")
def landing_page():
    auth_on = bool(os.getenv("PANEL_USERNAME", "").strip() and os.getenv("PANEL_PASSWORD", "").strip())
    return render_template(
        "landing.html",
        error=_HATA.get(request.args.get("hata")),
        next=_safe_next(request.args.get("next")),
        direct=(not auth_on) or bool(session.get("lal_auth")),
    )


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

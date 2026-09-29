"""
blueprints/landing_routes.py
--------------------------------
Herkese açık tanıtım (landing) sayfası. Panel route'larına dokunmaz.
app.py'de kayıt: app.register_blueprint(landing_routes_bp)
"""

from flask import Blueprint, render_template

bp = Blueprint("landing_routes", __name__)


@bp.route("/tanitim")
def landing_page():
    return render_template("landing.html")

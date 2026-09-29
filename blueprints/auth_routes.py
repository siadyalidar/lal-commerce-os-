"""
blueprints/auth_routes.py
--------------------------------
Panel girisi (oturum tabanli). /giris formu PANEL_USERNAME / PANEL_PASSWORD ile
dogrular, basarili olunca session["lal_auth"] set edilir. Basic Auth (Authorization
header) app.py'de yedek olarak korunur; testler ve scriptler onunla calismaya devam eder.
Brute-force korumasi: ayni IP'den 5 hatali denemeden sonra 5 dk kilit (bellekte).
"""

import os
import secrets
import time
from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, session

bp = Blueprint("auth_routes", __name__)

MAX_FAILS = 5
LOCK_SECONDS = 300
_fails = {}


def _safe_next(target):
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/panel"
    p = urlparse(target)
    if p.scheme or p.netloc or p.path in ("/giris", "/cikis"):
        return "/panel"
    return target


def _same(a, b):
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@bp.route("/giris", methods=["GET", "POST"])
def login():
    user = os.getenv("PANEL_USERNAME", "").strip()
    pw = os.getenv("PANEL_PASSWORD", "").strip()
    nxt = _safe_next(request.values.get("next"))
    if not (user and pw) or session.get("lal_auth"):
        return redirect(nxt)
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        now = time.time()
        count, last = _fails.get(ip, (0, 0.0))
        if now - last > LOCK_SECONDS:
            count = 0
        if count >= MAX_FAILS:
            error = "Çok fazla hatalı deneme. Birkaç dakika sonra tekrar dene."
        elif _same(request.form.get("username", "").strip(), user) and _same(request.form.get("password", ""), pw):
            _fails.pop(ip, None)
            session.clear()
            session["lal_auth"] = True
            return redirect(nxt)
        else:
            _fails[ip] = (count + 1, now)
            error = "Kullanıcı adı veya parola hatalı."
    return render_template("giris.html", error=error, next=nxt), (401 if error else 200)


@bp.route("/cikis")
def logout():
    session.clear()
    return redirect("/")

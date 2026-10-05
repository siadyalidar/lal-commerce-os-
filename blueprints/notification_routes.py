"""Bildirim merkezi API'si. UI bu uc noktalari periyodik sorgular."""

from flask import Blueprint, jsonify, request

import notifications

bp = Blueprint("notification_routes", __name__)


@bp.route("/api/notifications")
def notifications_list():
    try:
        after_id = int(request.args.get("after_id", 0))
        limit = max(1, min(int(request.args.get("limit", 30)), 100))
    except ValueError:
        return jsonify({"error": "gecersiz parametre"}), 400
    try:
        return jsonify(notifications.list_notifications(after_id=after_id, limit=limit))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/notifications/read", methods=["POST"])
def notifications_read():
    payload = request.get_json(silent=True) or {}
    try:
        if payload.get("all"):
            notifications.mark_read(None)
        else:
            ids = [int(i) for i in payload.get("ids", [])]
            notifications.mark_read(ids)
    except (TypeError, ValueError):
        return jsonify({"error": "gecersiz id"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})

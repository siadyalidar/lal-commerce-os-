"""Bildirim dedektoru Celery gorevi (beat: her 5 dakikada bir)."""

from celery_app import celery_app

import notifications


@celery_app.task(name="notification_tasks.detect_notifications")
def detect_notifications():
    return notifications.detect_and_notify()

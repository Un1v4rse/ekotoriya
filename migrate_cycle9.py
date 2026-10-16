#!/usr/bin/env python3
"""Миграция «цикл 9» (цикл 4 / этап 7): чат клиент↔магазин —
таблицы chat_dialogs и chat_messages. Идемпотентна.
Запуск: из app.py при старте или standalone.
"""
from sqlalchemy import text

import db as db_module
import models  # noqa: F401  — регистрирует метаданные
from db import db


def migrate(app=None):
    if app is None:
        app = db_module.standalone_app()
    with app.app_context():
        db.create_all()
        try:
            db.session.execute(text(
                "CREATE TABLE IF NOT EXISTS chat_dialogs ("
                "id INTEGER PRIMARY KEY, "
                "customer_id INTEGER NOT NULL, "
                "status VARCHAR(20) DEFAULT 'open', "
                "created_at DATETIME, "
                "updated_at DATETIME)"))
            db.session.commit()
        except Exception:
            db.session.rollback()  # таблица уже есть
        try:
            db.session.execute(text(
                "CREATE TABLE IF NOT EXISTS chat_messages ("
                "id INTEGER PRIMARY KEY, "
                "dialog_id INTEGER NOT NULL, "
                "sender VARCHAR(20) DEFAULT 'client', "
                "text TEXT DEFAULT '', "
                "read_by_admin BOOLEAN DEFAULT 0, "
                "read_by_client BOOLEAN DEFAULT 0, "
                "created_at DATETIME)"))
            db.session.commit()
        except Exception:
            db.session.rollback()  # таблица уже есть
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle9] готово: chat_dialogs, chat_messages')

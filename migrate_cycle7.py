#!/usr/bin/env python3
"""Миграция «цикл 7»: таблица feedback_requests — заявки с кнопок конструктора
(действие «форма заявки»). Идемпотентна (CREATE TABLE IF NOT EXISTS).
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
                "CREATE TABLE IF NOT EXISTS feedback_requests ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR(200) DEFAULT '', "
                "phone VARCHAR(60) DEFAULT '', "
                "message TEXT DEFAULT '', "
                "page VARCHAR(300) DEFAULT '', "
                "created_at DATETIME)"))
            db.session.commit()
        except Exception:
            db.session.rollback()  # таблица уже есть
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle7] готово: feedback_requests')

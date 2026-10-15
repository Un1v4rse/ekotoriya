#!/usr/bin/env python3
"""Миграция «цикл 8» (цикл 4 / этап 6): таблица reviews — отзывы покупателей.
approved=False по умолчанию (модерация в админке). Идемпотентна.
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
                "CREATE TABLE IF NOT EXISTS reviews ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR(200) NOT NULL, "
                "rating INTEGER DEFAULT 5, "
                "text TEXT DEFAULT '', "
                "product_name VARCHAR(500) DEFAULT '', "
                "approved BOOLEAN DEFAULT 0, "
                "created_at DATETIME)"))
            db.session.commit()
        except Exception:
            db.session.rollback()  # таблица уже есть
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle8] готово: reviews')

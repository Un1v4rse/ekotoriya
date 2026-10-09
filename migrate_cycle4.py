#!/usr/bin/env python3
"""Миграция «цикл 4»: events.payload — JSON-снапшот товара в событии like.

Нужен, чтобы /personal/favorite/ показывал лайкнутые товары, которых нет
в каталоге сайта (статичные aspro-карточки разделов), — из снапшота.
Идемпотентна (ALTER в try/except). Запуск: из app.py при старте или standalone.
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
                "ALTER TABLE events ADD COLUMN payload TEXT DEFAULT ''"))
            db.session.commit()
        except Exception:
            db.session.rollback()  # колонка уже есть
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle4] готово: events.payload')

#!/usr/bin/env python3
"""Миграция «цикл 5»: products.manual_overrides — JSON-список полей, вручную
изменённых в админке. Синхронизации WB/Ozon не перезаписывают закреплённые
поля (защита ручных правок от затирания данными маркетплейса).
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
                "ALTER TABLE products ADD COLUMN manual_overrides TEXT"))
            db.session.commit()
        except Exception:
            db.session.rollback()  # колонка уже есть
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle5] готово: products.manual_overrides')

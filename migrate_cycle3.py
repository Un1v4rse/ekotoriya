#!/usr/bin/env python3
"""Миграция «цикл 3»: products.site_section — раздел сайта для товара.

Пустая строка = товар показывается везде (обратная совместимость).
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
                'ALTER TABLE products ADD COLUMN site_section VARCHAR(60) DEFAULT \'\''))
            db.session.commit()
        except Exception:
            db.session.rollback()  # колонка уже есть
        try:
            db.session.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_products_site_section ON products (site_section)'))
            db.session.commit()
        except Exception:
            db.session.rollback()
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle3] готово: products.site_section')

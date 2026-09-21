#!/usr/bin/env python3
"""
Миграция «цикл 2» конструктора (CN-T5/CN-T6/CN-T7/CN-T9/CN-T10/CN-T12).

Добавляет:
    pages.published_blocks JSON  — опубликованные блоки (backfill из blocks);
    pages.settings JSON          — настройки страницы (page-level);
    таблицу page_versions        — снапшоты версий страниц (модель PageVersion);
    таблицу block_templates      — «Мои блоки» (модель BlockTemplate).

Идемпотентна: повторный запуск ничего не ломает (ALTER в try/except,
db.create_all() создаёт только отсутствующие таблицы).

Зуск:
    python migrate_cycle2.py              — standalone (свой контекст БД)
    из app.py при старте — migrate_cycle2.migrate() в защищённом try/except.
"""
from sqlalchemy import text

import db as db_module
import models  # noqa: F401  — регистрирует PageVersion/BlockTemplate в метаданных
from db import db


def migrate(app=None):
    """Применить миграцию. app=None — создаёт standalone-контекст сама."""
    if app is None:
        app = db_module.standalone_app()
    with app.app_context():
        # Новые таблицы (page_versions, block_templates) — только если их нет.
        db.create_all()
        # Новые колонки pages (SQLite: ADD COLUMN, существующие пропускаем).
        for stmt in (
            'ALTER TABLE pages ADD COLUMN published_blocks JSON',
            'ALTER TABLE pages ADD COLUMN settings JSON',
        ):
            try:
                db.session.execute(text(stmt))
                db.session.commit()
            except Exception:
                db.session.rollback()   # колонка уже есть (или таблицы ещё нет)
        # Backfill: опубликованным считаем текущее состояние blocks.
        try:
            db.session.execute(text(
                'UPDATE pages SET published_blocks = blocks '
                'WHERE published_blocks IS NULL AND blocks IS NOT NULL'))
            db.session.commit()
        except Exception:
            db.session.rollback()
    return True


if __name__ == '__main__':
    migrate()
    print('[migrate_cycle2] готово: pages.published_blocks/settings, page_versions, block_templates')

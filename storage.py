#!/usr/bin/env python3
"""
Слой хранения контента сайта на БД (AR-1, задача 1.6).

Заменяет прямое чтение/запись data/content.json:
  - load_content()  — собирает прежний dict {menu, pages, site, socials} из БД;
                      пока БД пуста (миграция не прогнана), читает JSON-файл
                      как фолбэк — сайт не ломается на промежуточном этапе.
  - save_content()  — раскладывает dict обратно в таблицы pages/site_settings
                      (транзакционно, upsert по slug/key).

Формат возвращаемого dict полностью повторяет старый content.json —
шаблоны, админка и редактор страниц не меняются.
"""
import copy
import json
import threading
from pathlib import Path

from db import db
from models import Page, SiteSetting

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'content.json'

SETTINGS_KEYS = ('site', 'menu', 'socials')

_lock = threading.Lock()
_cache = {'content': None, 'mtime': None}


def _read_json_fallback():
    if not DATA_FILE.exists():
        return {'menu': [], 'pages': [], 'site': {}, 'socials': []}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def db_has_content():
    """True, если в БД уже есть страницы (миграция прогнана)."""
    try:
        return db.session.query(Page.id).first() is not None
    except Exception:
        db.session.rollback()
        return False


def load_content():
    """Полный dict контента (глубокая копия — вызывающий может менять свободно)."""
    with _lock:
        if _cache['content'] is not None:
            return copy.deepcopy(_cache['content'])

        if not db_has_content():
            content = _read_json_fallback()
            return content

        settings = {s.key: s.value for s in db.session.query(SiteSetting).all()}
        pages = [p.to_content_dict() for p in db.session.query(Page).order_by(Page.id).all()]
        content = {
            'menu': settings.get('menu') or [],
            'socials': settings.get('socials') or [],
            'site': settings.get('site') or {},
            'pages': pages,
        }
        _cache['content'] = copy.deepcopy(content)
        return copy.deepcopy(content)


def invalidate_cache():
    with _lock:
        _cache['content'] = None


def save_content(data):
    """Сохранить dict контента в БД. Структура — как у старого content.json."""
    with _lock:
        try:
            for key in SETTINGS_KEYS:
                if key in data:
                    row = db.session.get(SiteSetting, key)
                    if row is None:
                        row = SiteSetting(key=key, value=data[key])
                        db.session.add(row)
                    else:
                        row.value = copy.deepcopy(data[key])

            incoming = {p['slug']: p for p in data.get('pages', []) if p.get('slug')}
            existing = {p.slug: p for p in db.session.query(Page).all()}

            for slug, pdata in incoming.items():
                page = existing.get(slug)
                if page is None:
                    page = Page(slug=slug)
                    db.session.add(page)
                page.url = pdata.get('url', page.url or '/')
                page.title = pdata.get('title', '')
                page.h1 = pdata.get('h1', '')
                page.meta_description = pdata.get('meta_description', '')
                page.meta_keywords = pdata.get('meta_keywords', '')
                page.canonical = pdata.get('canonical', '')
                page.template = pdata.get('template', '')
                if isinstance(pdata.get('blocks'), list):
                    page.blocks = copy.deepcopy(pdata['blocks'])
                if pdata.get('content_html') is not None:
                    page.content_html = pdata.get('content_html', '')
                if pdata.get('updated_at'):
                    # CN-5: явный updated_at (контроль версий страницы при page-save)
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(str(pdata['updated_at']))
                        page.updated_at = dt.replace(tzinfo=None) if dt.tzinfo else dt
                    except ValueError:
                        pass

            # Страницы, которых нет в новом наборе, — удалённые.
            for slug, page in existing.items():
                if slug not in incoming:
                    db.session.delete(page)

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        _cache['content'] = None


def get_setting(key, default=None):
    row = db.session.get(SiteSetting, key)
    return copy.deepcopy(row.value) if row and row.value is not None else default


def set_setting(key, value):
    row = db.session.get(SiteSetting, key)
    if row is None:
        row = SiteSetting(key=key, value=copy.deepcopy(value))
        db.session.add(row)
    else:
        row.value = copy.deepcopy(value)
    db.session.commit()
    invalidate_cache()

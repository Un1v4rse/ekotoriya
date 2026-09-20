# -*- coding: utf-8 -*-
"""Производительность (Этап 6.2): контент кэшируется (нет повторных запросов
в БД), рендер главной/каталога не плодит запросы в цикле (N+1), ключевые
маршруты отдаются с заголовками кэша. Нагрузочный тест TS-4 — scripts/load_test.py."""
from sqlalchemy import event

import app as app_module
import storage
import wb_sync


def _count_queries(engine, fn):
    """Выполнить fn, вернуть число SQL-запросов к БД."""
    state = {'count': 0}

    @event.listens_for(engine, 'before_cursor_execute')
    def _count(conn, cursor, statement, parameters, context, executemany):
        state['count'] += 1

    try:
        fn()
    finally:
        event.remove(engine, 'before_cursor_execute', _count)
    return state['count']


def test_load_content_uses_cache_not_queries(app_context):
    """Повторный load_content() — 0 запросов к БД (кэш storage, AR-3)."""
    engine = app_context.extensions['sqlalchemy'].engine
    storage.load_content()
    n = _count_queries(engine, lambda: storage.load_content())
    assert n == 0


def test_catalog_page_no_n_plus_1(app_context):
    """Рендер /catalog/ без запросов в цикле: меньше порога даже на пустой БД."""
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    client = app_module.app.test_client()
    engine = app_context.extensions['sqlalchemy'].engine
    n = _count_queries(engine, lambda: client.get('/catalog/'))
    assert n <= 40, f'подозрение на N+1: {n} запросов за рендер каталога'
    # повторный запрос — кэш (продукты/популярное), запросов заметно меньше
    n2 = _count_queries(engine, lambda: client.get('/catalog/'))
    assert n2 < n


def test_index_and_catalog_cache_headers(app_context):
    """Статика с long-term cache, HTML — no-store (add_cache_headers не сломан)."""
    wb_sync.sync(token=None, demo=True)
    client = app_module.app.test_client()
    r = client.get('/static/noimage.png')
    assert 'max-age=31536000' in r.headers.get('Cache-Control', '')
    r = client.get('/')
    assert r.status_code == 200
    assert 'no-store' in r.headers.get('Cache-Control', '')
    r = client.get('/catalog/')
    assert r.status_code == 200
    assert 'no-store' in r.headers.get('Cache-Control', '')

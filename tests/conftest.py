# -*- coding: utf-8 -*-
"""Общие фикстуры тестов (TS-1).

Изоляция: отдельная временная SQLite-БД (env DATABASE_URL выставляется
ДО импорта модулей проекта), JSON-фолбэки wb_sync перенаправлены во tmp,
контент сидится через storage.save_content — тесты не трогают data/*.json.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

_TMP_DB = Path(tempfile.mkdtemp(prefix='ekotoriya-tests-')) / 'test.db'
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB}'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('CACHE_TYPE', 'SimpleCache')

import storage  # noqa: E402
import wb_sync  # noqa: E402
from db import db  # noqa: E402

MINIMAL_CONTENT = {
    'menu': [{'title': 'Главная', 'url': '/'}],
    'socials': [],
    'site': {
        'name': 'Экотория (тест)',
        'phone': '+7 900 000-00-00',
        'email': 'test@example.com',
        'wb_seller_id': '175521',
    },
    'pages': [
        {
            'slug': 'home',
            'url': '/',
            'title': 'Главная',
            'h1': 'Экотория',
            'meta_description': '',
            'template': 'pages/index.html',
            'blocks': [{'type': 'heading', 'level': 2, 'text': 'Привет'}],
        },
        {
            'slug': 'catalog',
            'url': '/catalog/',
            'title': 'Каталог',
            'template': 'pages/catalog.html',
            'blocks': [{'type': 'heading', 'level': 2, 'text': 'Каталог товаров'}],
        },
        {
            'slug': 'catalog__namatrasniki',
            'url': '/catalog/namatrasniki/',
            'title': 'Наматрасники',
            'template': 'pages/catalog__namatrasniki.html',
            'blocks': [],
        },
    ],
}


@pytest.fixture(autouse=True)
def app_context(tmp_path, monkeypatch):
    """Контекст Flask-приложения, свежая БД и изолированные файлы wb_sync."""
    monkeypatch.setattr(wb_sync, 'PRODUCTS_FILE', tmp_path / 'wb_products.json')
    monkeypatch.setattr(wb_sync, 'SYNC_LOG_FILE', tmp_path / 'sync_log.json')
    monkeypatch.setattr(wb_sync, 'DATA_FILE', tmp_path / 'content.json')

    import app as app_module
    application = app_module.app

    with application.app_context():
        db.drop_all()
        db.create_all()
        storage.invalidate_cache()
        storage.save_content(MINIMAL_CONTENT)
        yield application
        db.session.remove()
        db.drop_all()
        storage.invalidate_cache()

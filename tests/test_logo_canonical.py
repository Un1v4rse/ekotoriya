# -*- coding: utf-8 -*-
"""Цикл 4, этап 4: одно каноническое лого во всех точках (PG-4)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module

CANON = app_module.CANONICAL_LOGO


def test_versioned_logo_appends_once():
    assert app_module._versioned_logo(CANON) == CANON + '?v=4'
    assert app_module._versioned_logo(CANON + '?v=4') == CANON + '?v=4'
    # кастомный лого админа не трогаем
    assert app_module._versioned_logo('/static/upload/my.png') == '/static/upload/my.png'
    assert app_module._versioned_logo('') == ''


def test_absolute_url_keeps_query():
    url = app_module._absolute_url(CANON + '?v=4', 'http://localhost:5000/')
    assert url == 'http://localhost:5000' + CANON + '?v=4'


def test_homepage_uses_single_canonical_logo(app_context):
    """На отрендеренной главной все ссылки на лого — канонический файл,
    ссылок на домен оригинала или посторонние лого нет."""
    client = app_context.test_client()
    html = client.get('/').get_data(as_text=True)
    assert 'ekotoriya.ru/upload/' not in html
    # все упоминания файла лого — один и тот же канонический путь (с версией)
    import re
    refs = set(re.findall(r'(?:src|content)="([^"]*04g8dplb3dqudbalhuo0ryren6a5dklv[^"]*)"', html))
    assert refs and all(r.split('?')[0] == CANON for r in refs), refs
    assert '/static/uploads/logo.png' not in html
    assert 'favicon.ico?v=4' in html

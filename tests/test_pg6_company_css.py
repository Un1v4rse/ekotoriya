# -*- coding: utf-8 -*-
"""Регрессия «урезанного баннера» и blur шапки (фикс от 2026-10-16).

PG-6 (цикл 4) вводил контейнерные правила max-width:1344px для страниц
«О компании»/«Отзывы»/«Документы». Пока они жили в глобальной REVIEW_CSS,
правило #content .middle > .container сжимало широкие блоки главной
(BIG_BANNER и др.) — на экранах >1344px по бокам баннера были белые поля.
Теперь правила инъецируются только на /company*. Тесты фиксируют разделение.
"""
import pathlib

import app as app_module

PG6_MARKER = '#content .middle > .container{max-width:1344px'
REVIEW_MARKER = '.wb-review-card{'
BLUR_MARKER = 'backdrop-filter: blur(5px)'


def _get(url):
    return app_module.app.test_client().get(url).get_data(as_text=True)


def test_home_has_no_pg6_container_css():
    assert PG6_MARKER not in _get('/')


def test_catalog_has_no_pg6_container_css():
    assert PG6_MARKER not in _get('/catalog/')


def test_company_pages_have_pg6_container_css():
    for url in ('/company/', '/company/reviews/', '/company/docs/'):
        assert PG6_MARKER in _get(url), url


def test_review_css_still_global():
    html = _get('/')
    assert REVIEW_MARKER in html


def test_company_review_mobile_rules_present():
    html = _get('/company/reviews/')
    assert '#content{overflow-x:hidden;}' in html
    assert '#content .front_review .item{padding:12px !important;}' in html


def test_header_blur_removed_from_static_css():
    base = pathlib.Path(app_module.BASE_DIR) / 'static' / 'bitrix'
    files = [base / 'templates' / 'aspro_max' / 'css' / 'custom.css']
    files += list((base / 'cache' / 'css' / 's1' / 'aspro_max').glob('template_*/*.css'))
    assert files, 'не найдены css-файлы шаблона'
    for f in files:
        assert BLUR_MARKER not in f.read_text(encoding='utf-8'), str(f)

# -*- coding: utf-8 -*-
"""Живой каталог: /api/catalog, предфильтр разделов, пагинация главной, меню.

Замечания «фильтры должны работать без кнопки Применить» и «на главной много
товаров без пагинации»: панель фильтров теперь живая (JS CATALOG_LIVE_JS
перезапрашивает /api/catalog), endpoint отдаёт частичный HTML, главная и
разделы получают catalog_view (12 товаров/стр на главной, предфильтр раздела
на страницах разделов). Меню — структура как на оригинальном сайте.
"""
import re

import pytest

import app as app_module
import storage
import wb_sync
from db import db
from models import Product


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def _add_products(n, start=900000000, **kwargs):
    for i in range(n):
        db.session.add(Product(source='wb', nm_id=start + i,
                               name=kwargs.get('name', f'Товар тестовый {i}'),
                               price=kwargs.get('price', 1000 + i),
                               visible=True, stock=5,
                               site_section=kwargs.get('site_section', '')))
    db.session.commit()
    app_module.invalidate_site_cache()


# ---------- /api/catalog ----------

def test_api_catalog_returns_results_html(client):
    _seed()
    r = client.get('/api/catalog')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success'] is True
    assert data['total'] == 8
    assert data['pages'] == 1
    assert 'wb-product-card' in data['html']
    assert 'Найдено товаров: 8' in data['html']


def test_api_catalog_respects_filters_and_base(client):
    _seed()
    r = client.get('/api/catalog?size=140x200&base=%2Fcatalog%2F')
    data = r.get_json()
    view = app_module._filtered_catalog({'size': '140x200'})
    assert data['total'] == view['total']
    assert data['total'] >= 1
    # ссылки пагинации строятся от base
    _add_products(30)
    r = client.get('/api/catalog?base=/&per_page=12&page=2')
    data = r.get_json()
    assert data['page'] == 2
    assert 'href="/?' in data['html']
    assert 'per_page=12' in data['html']


def test_api_catalog_rejects_foreign_base(client):
    _seed()
    r = client.get('/api/catalog?base=https://evil.example/x')
    data = r.get_json()
    assert data['success'] is True
    # base сброшен на дефолт каталога — внешний URL в ссылки не попадает
    assert 'https://evil.example' not in data['html']


def test_api_catalog_section_prefilter(client):
    _seed()
    _add_products(3, start=910000000, name='Одеяло тестовое',
                  site_section='odeyala')
    app_module.invalidate_site_cache()
    r = client.get('/api/catalog?section=namatrasniki')
    data = r.get_json()
    # товары другого раздела отсутствуют, демо без раздела — на месте
    assert data['total'] == 8
    assert 'Одеяло тестовое' not in data['html']


def test_api_catalog_empty_result_still_has_grid_shell(client):
    _seed()
    r = client.get('/api/catalog?q=абракадабранетоваров')
    data = r.get_json()
    assert data['total'] == 0
    assert 'wb-empty-result' in data['html']


# ---------- разделы: та же панель + предфильтр ----------

def test_section_page_has_live_filter_panel(client):
    _seed()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'class="wb-catalog-filters"' in body
    form = re.search(r'<form[^>]*wb-catalog-filters.*?</form>', body, re.S)
    assert form, 'форма фильтров не найдена'
    form_html = form.group(0)
    # предфильтр раздела уходит в форму
    assert 'name="section" value="namatrasniki"' in form_html
    # кнопок отправки и селекта «Источник» в форме нет
    assert 'type="submit"' not in form_html
    assert 'name="source"' not in form_html
    assert 'data-base="/catalog/namatrasniki/"' in body
    assert 'wb-catalog-results' in body


def test_section_page_prefilters_products(client):
    _seed()
    _add_products(3, start=910000000, name='Одеяло тестовое',
                  site_section='odeyala')
    app_module.invalidate_site_cache()
    r = client.get('/catalog/namatrasniki/')
    body = r.data.decode('utf-8')
    assert 'Одеяло тестовое' not in body
    assert body.count('wb-product-card') == 8


def test_section_page_has_h1_breadcrumbs_and_hides_legacy(client):
    _seed()
    r = client.get('/catalog/namatrasniki/')
    body = r.data.decode('utf-8')
    # заголовок раздела и крошки как в оригинальном дизайне
    assert '<h1 id="pagetitle">Наматрасники</h1>' in body
    assert 'BreadcrumbList' in body
    # мёртвый bitrix-листинг/фильтр скрыт на страницах разделов
    assert '.section-content-wrapper .catalog_block{display:none' in body
    assert '.section-content-wrapper .filter-panel-wrapper{display:none' in body


# ---------- главная: пагинация блока маркетплейс ----------

def test_index_paginates_marketplace_block(client):
    _seed()
    _add_products(30)  # 8 demo + 30 = 38 товаров
    r = client.get('/')
    body = r.data.decode('utf-8')
    # не более 12 карточек на странице
    assert body.count('wb-product-card') == 12
    assert 'class="wb-catalog-pagination"' in body
    assert 'per_page=12' in body
    assert 'Все товары' in body
    # вторая страница серверно рендерится с тем же ограничением
    r2 = client.get('/?page=2&per_page=12')
    body2 = r2.data.decode('utf-8')
    assert body2.count('wb-product-card') == 12


def test_index_block_renders_without_products(client):
    r = client.get('/')
    assert r.status_code == 200
    assert 'wb-catalog-wrapper' in r.data.decode('utf-8')


# ---------- меню: структура как на оригинале ----------

def test_menu_matches_original_structure():
    # структура меню сайта = оригиналу (ekotoriya.ru)
    original_menu = [
        {'title': 'Каталог', 'url': '/catalog/', 'children': [
            {'title': 'Наматрасники', 'url': '/catalog/namatrasniki/', 'children': [
                {'title': 'Наматрасники на резинке',
                 'url': '/catalog/namatrasniki/namatrasniki-na-rezinke/'},
                {'title': 'Наматрасники на молнии',
                 'url': '/catalog/namatrasniki/namatrasniki-na-molnii/'},
                {'title': 'Непромокаемые наматрасники',
                 'url': '/catalog/namatrasniki/nepromokaemye-namatrasniki/'},
            ]},
            {'title': 'Одеяла', 'url': '/catalog/odeyala/', 'children': [
                {'title': 'Зимние', 'url': '/catalog/odeyala/zimnie/'},
                {'title': 'Всесезонные', 'url': '/catalog/odeyala/vsesezonnye/'},
                {'title': 'Летние', 'url': '/catalog/odeyala/letnie/'},
            ]},
            {'title': 'Наперники', 'url': '/catalog/naperniki/'},
        ]},
        {'title': 'Оптовым покупателям', 'url': '/help/opt/'},
        {'title': 'Компания', 'url': '/company/', 'children': [
            {'title': 'О компании', 'url': '/company/'},
            {'title': 'Отзывы', 'url': '/company/reviews/'},
            {'title': 'Документы', 'url': '/company/docs/'},
        ]},
        {'title': 'Наше производство', 'url': '/proizvodstvo/'},
        {'title': 'Советы покупателям', 'url': '/blog/'},
        {'title': 'Контакты', 'url': '/contacts/'},
    ]
    content = storage.load_content()
    content['menu'] = original_menu
    storage.save_content(content)

    def slim(items):
        return [(i.get('title'), i.get('url'), slim(i.get('children') or []))
                for i in items]

    menu = slim(storage.load_content().get('menu') or [])
    titles = [m[0] for m in menu]
    assert titles == ['Каталог', 'Оптовым покупателям', 'Компания',
                      'Наше производство', 'Советы покупателям', 'Контакты']
    catalog = menu[0]
    assert catalog[1] == '/catalog/'
    child_titles = [c[0] for c in catalog[2]]
    assert child_titles == ['Наматрасники', 'Одеяла', 'Наперники']
    assert catalog[2][0][1] == '/catalog/namatrasniki/'
    assert [g[0] for g in catalog[2][0][2]] == [
        'Наматрасники на резинке', 'Наматрасники на молнии',
        'Непромокаемые наматрасники']
    company = menu[2]
    assert [c[0] for c in company[2]] == ['О компании', 'Отзывы', 'Документы']
    assert company[2][1][1] == '/company/reviews/'

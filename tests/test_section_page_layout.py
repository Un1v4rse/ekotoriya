# -*- coding: utf-8 -*-
"""Категорийные страницы (/catalog/<раздел>/) в дизайне оригинала: плитки
подкатегорий, панель «КАТАЛОГ | сортировка | вид», чипы фильтров, сетка
4 колонки с ценами «₽/шт» и бейджем экономии. Живые фильтры (/api/catalog)
и страницы /catalog/ и главной не затрагиваются."""
import pytest

import app as app_module
import storage
import wb_sync


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture()
def demo_products():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    return [p['nm_id'] for p in wb_sync.load_products()]


MENU_WITH_SUBS = [
    {
        'title': 'Каталог',
        'url': '/catalog/',
        'children': [
            {
                'title': 'Наматрасники',
                'url': '/catalog/namatrasniki/',
                'children': [
                    {'title': 'Наматрасники на резинке',
                     'url': '/catalog/namatrasniki/namatrasniki-na-rezinke/'},
                    {'title': 'Наматрасники на молнии',
                     'url': '/catalog/namatrasniki/namatrasniki-na-molnii/'},
                ],
            },
        ],
    },
]


def _seed_menu():
    content = storage.load_content()
    content['menu'] = MENU_WITH_SUBS
    storage.save_content(content)


def test_section_page_layout_markup(client, demo_products):
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # раскладка с левым меню
    assert 'grid-template-columns:262px minmax(0,1fr)' in html
    # левое меню развёрнуто до первого уровня
    assert '.slide-block__body{display:block !important;}' in html
    # панель раздела и чипы фильтров
    assert 'wb-sec-filters' in html
    assert 'КАТАЛОГ' in html
    assert 'wb-sec-panel__sort' in html
    assert 'wb-chip' in html
    assert 'Размер, см' in html
    # сетка раздела с ценами оригинала (копейки, ₽/шт, экономия)
    assert 'wb-sec-results' in html
    assert 'wb-sec-card' in html
    assert 'Экономия' in html
    assert '.00 ₽' in html
    # кнопки ТЗ на карточках
    assert html.count('wb-like-btn') >= 8
    assert html.count('wb-cart-btn') >= 8
    assert html.count('wb-buy-btn') >= 8


def test_section_page_subcategory_tiles(client, demo_products):
    _seed_menu()
    r = client.get('/catalog/namatrasniki/')
    html = r.get_data(as_text=True)
    assert 'wb-sec-subcats' in html
    assert 'Наматрасники на молнии' in html
    assert 'Наматрасники на резинке' in html
    # счётчики посчитаны из демо-товаров по ключевым словам имени
    assert '6 товара' in html   # на молнии
    assert '2 товара' in html   # на резинке
    # ссылки плиток ведут на подразделы
    assert '/catalog/namatrasniki/namatrasniki-na-molnii/' in html


def test_api_catalog_keeps_section_layout(client, demo_products):
    r = client.get('/api/catalog?section=namatrasniki&sort=price_desc')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success']
    assert 'wb-sec-card' in data['html']
    assert 'Экономия' in data['html']
    # сортировка применена: первый товар не дешевле последнего
    import re
    prices = [float(m.replace(' ', ''))
              for m in re.findall(r'([\d ]+\.\d{2}) ₽/шт', data['html'])]
    assert prices == sorted(prices, reverse=True)


def test_catalog_and_home_pages_untouched(client, demo_products):
    r = client.get('/catalog/')
    html = r.get_data(as_text=True)
    assert 'wb-sec-filters' not in html
    assert 'wb-sec-card' not in html
    assert 'grid-template-columns:262px' not in html
    assert 'wb-catalog-filters' in html  # прежняя панель фильтров

    r = client.get('/')
    html = r.get_data(as_text=True)
    assert 'wb-sec-card' not in html
    assert 'grid-template-columns:262px' not in html


def test_money_template_global():
    assert app_module.app.jinja_env.globals.get('money') is app_module._money
    assert app_module._money(4146.2) == '4 146.20'
    assert app_module._money(3990, kopecks=False) == '3 990'

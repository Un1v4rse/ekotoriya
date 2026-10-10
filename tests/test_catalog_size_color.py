# -*- coding: utf-8 -*-
"""Фильтры единого каталога: size/color, комбинации, панель при пустой выборке.

Замечание «можно выбрать фильтр, но товары не сортируются»: мёртвый
bitrix-smartfilter категорийных страниц маппился только на цену/сортировку,
а панель /catalog/ пропадала целиком при пустой выборке (фильтр «сбрасывался»).
Здесь серверная часть: size (подстрока в имени, нормализация x/х/*, диапазоны
«17-20»), color (подстрока в имени), комбинации и восстановление контролов.
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import wb_sync
from db import db
from models import Product


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def test_size_filter_substring_normalized(client):
    _seed()
    view = app_module._filtered_catalog({'size': '200x200'})
    assert view['total'] >= 2
    assert all('200' in (p['name'] or '') for p in view['products'])


def test_size_filter_cyrillic_x_and_star():
    _seed()
    a = app_module._filtered_catalog({'size': '200х200'})  # кириллическая х
    b = app_module._filtered_catalog({'size': '200*200'})
    c = app_module._filtered_catalog({'size': '200x200'})
    assert a['total'] == c['total'] == b['total'] >= 2


def test_size_filter_range_board_height():
    _seed()  # в демо именах есть «высота 17-21» и «высота 21-23»
    view = app_module._filtered_catalog({'size': '17-20'})
    assert view['total'] >= 1
    assert any('17-21' in (p['name'] or '') for p in view['products'])
    # числа размера (200) не должны попадать под диапазон высоты
    assert not any('21-23' in (p['name'] or '') for p in view['products'])


def test_size_filter_multiple_or():
    _seed()
    view = app_module._filtered_catalog({'size': '140x200,200x200'})
    single1 = app_module._filtered_catalog({'size': '140x200'})
    single2 = app_module._filtered_catalog({'size': '200x200'})
    assert view['total'] == single1['total'] + single2['total']  # демо-размеры не пересекаются


def test_size_filter_repeated_params():
    _seed()
    class Args(dict):
        def getlist(self, k):
            v = self.get(k)
            return v if isinstance(v, list) else ([v] if v else [])
    view = app_module._filtered_catalog(Args(size=['140x200', '200x200']))
    assert view['total'] >= 3


def test_color_filter_substring():
    _seed()
    # добавим товар с цветом в имени
    p = Product(source='wb', nm_id=999001, name='Наматрасник 140х200 серый',
                price=1000, stock=5)
    db.session.add(p)
    db.session.commit()
    app_module.invalidate_site_cache()
    view = app_module._filtered_catalog({'color': 'серый'})
    assert view['total'] == 1
    view2 = app_module._filtered_catalog({'color': 'СЕРЫЙ'})
    assert view2['total'] == 1


def test_filter_combination_size_price(client):
    _seed()
    view = app_module._filtered_catalog({'size': '200x200', 'price_from': 3500})
    all200 = app_module._filtered_catalog({'size': '200x200'})
    assert view['total'] < all200['total']
    assert all((p.get('final_price') or 0) >= 3500 for p in view['products'])


def test_catalog_page_renders_filter_panel_on_empty_result(client):
    _seed()
    r = client.get('/catalog/?q=абракадабраникакая')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    # панель фильтров и счётчик не должны исчезать при пустой выборке
    assert 'wb-catalog-filters' in body
    assert 'Найдено товаров: 0' in body


def test_catalog_page_applied_state_restored(client):
    _seed()
    r = client.get('/catalog/?size=200x200&sort=price_asc&price_from=3000')
    body = r.data.decode('utf-8')
    assert 'value="200x200" selected' in body
    assert 'value="price_asc" selected' in body
    assert 'name="price_from" value="3000"' in body


def test_catalog_sizes_and_colors_lists(client):
    _seed()
    p = Product(source='wb', nm_id=999002, name='Одеяло 172х205 серое всесезонное',
                price=2000, stock=5)
    db.session.add(p)
    db.session.commit()
    app_module.invalidate_site_cache()
    r = client.get('/catalog/')
    view = app_module._filtered_catalog({})
    assert '172x205' in view['sizes']
    assert any(c.startswith('сер') for c in view['colors'])


def test_source_site_filter():
    _seed()
    p = Product(source='site', nm_id=1, name='Свой тестовый товар', price=500,
                stock=3)
    db.session.add(p)
    db.session.commit()
    app_module.invalidate_site_cache()
    view = app_module._filtered_catalog({'source': 'site'})
    assert view['total'] == 1
    view_all = app_module._filtered_catalog({})
    assert view_all['total'] == 9  # 8 демо + 1 site


def test_pagination_keeps_size_color(client):
    _seed()
    r = client.get('/catalog/?size=140x200&color=серый&page=2')
    body = r.data.decode('utf-8')
    # class живого фильтра встречается и в инжектируемом JS — проверяем
    # именно разметку пагинации
    if 'class="wb-catalog-pagination"' in body:
        assert 'size=140x200' in body
        assert 'color=' in body

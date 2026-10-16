# -*- coding: utf-8 -*-
"""Вкладка «Товары» в админке — отдельная страница всех товаров (2026-10-16).

Список товаров (WB/Ozon/сайт) и массовые операции (скидка/раздел) вынесены
из вкладки «Интеграции» в собственную вкладку «Товары». Тест фиксирует
структуру admin.html: новая вкладка есть, старые контейнеры списков убраны.
"""
from werkzeug.security import generate_password_hash

import app as app_module


def _admin_client():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    client = app_module.app.test_client()
    client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    return client


def test_products_tab_exists():
    html = _admin_client().get('/admin/').get_data(as_text=True)
    assert 'data-tab="products"' in html
    assert 'id="tab-products"' in html
    assert 'id="all-products"' in html
    assert 'Товары</button>' in html
    assert 'use href="#i-box"' in html


def test_old_product_lists_removed_from_integrations():
    html = _admin_client().get('/admin/').get_data(as_text=True)
    # старые контейнеры списков во вкладке «Интеграции» удалены
    for marker in ('id="wb-products"', 'id="ozon-products"', 'id="site-products"',
                   'id="wb-search"', 'id="ozon-search"', 'id="site-search"',
                   'renderProductList('):
        assert marker not in html, marker
    # JS рендерит единый список
    assert 'function renderAllProducts()' in html
    assert 'id="prod-search"' in html
    assert 'name="prod-src"' in html


def test_bulk_controls_moved_to_products_tab():
    html = _admin_client().get('/admin/').get_data(as_text=True)
    for marker in ('id="sel-bulk-value"', 'id="sel-bulk-type"',
                   'id="sel-bulk-section"'):
        assert marker in html, marker
    # блок массовой скидки живёт внутри секции «Товары»
    section = html.split('id="tab-products"')[1].split('<section')[0]
    assert 'applySelectedDiscount()' in section
    assert 'applySelectedSection()' in section


def test_integrations_keeps_sync_settings():
    html = _admin_client().get('/admin/').get_data(as_text=True)
    # настройки синхронизации остались в «Интеграциях»
    assert 'id="int-sub-wb"' in html
    assert 'id="int-sub-ozon"' in html
    assert 'id="int-sub-discounts"' in html
    assert 'id="wb-token"' in html
    assert 'id="ozon-client-id"' in html
    assert 'applySelectedDiscount()' in html  # функция используется из «Товаров»

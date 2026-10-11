# -*- coding: utf-8 -*-
"""Защита ручных правок от перезаписи синхронизацией WB (manual_overrides).

Покрытие:
- ручное редактирование поля -> синк WB -> поле НЕ перезаписано, остальные обновились;
- снятие закрепления (product-unpin) -> синк -> поле снова обновляется;
- создание товара вручную через админ-API со всеми новыми полями;
- редактирование каждого нового поля через product-update;
- сохранение без изменений не закрепляет поля.
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import wb_sync
from db import db
from models import Product


@pytest.fixture()
def admin_client():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    client = app_module.app.test_client()
    resp = client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'test-csrf'
    return client


def _csrf():
    return {'X-CSRF-Token': 'test-csrf', 'Content-Type': 'application/json'}


def _seed():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    return wb_sync.load_products()[0]['nm_id']


def _persist_like_wb(nm, name, price, stock=5):
    """Имитация данных, приходящих из WB API (как в build_product)."""
    wb_sync._persist_products([{
        'id': nm, 'nm_id': nm, 'name': name, 'brand': 'БрендWB',
        'vendor_code': 'WB-1', 'price': price, 'discount_price': price,
        'wb_discount_percent': 10, 'photo': 'https://baskets.example/1.jpg',
        'url': f'https://www.wildberries.ru/catalog/{nm}/detail.aspx',
        'stock': stock, 'visible': True,
    }], demo=False)


def _get(nm):
    return db.session.query(Product).filter_by(source='wb', nm_id=nm).first()


def test_manual_edit_survives_wb_sync(admin_client):
    nm = _seed()
    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'product_id': nm,
                                'name': 'Название от админа'}, headers=_csrf())
    assert r.get_json()['success']
    assert 'name' in r.get_json()['manual_overrides']

    _persist_like_wb(nm, 'Название из WB', 9999, stock=42)

    prod = _get(nm)
    assert prod.name == 'Название от админа'      # закреплённое поле не перезаписано
    assert prod.price == 9999                     # остальные поля обновились
    assert prod.stock == 42
    assert prod.url.endswith(f'{nm}/detail.aspx')


def test_unpin_restores_wb_sync(admin_client):
    nm = _seed()
    admin_client.post('/admin/api/product-update',
                      json={'source': 'wb', 'product_id': nm,
                            'name': 'Название от админа'}, headers=_csrf())
    r = admin_client.post('/admin/api/product-unpin',
                          json={'source': 'wb', 'product_id': nm,
                                'fields': ['name']}, headers=_csrf())
    assert r.get_json()['success']
    assert r.get_json()['manual_overrides'] == []

    _persist_like_wb(nm, 'Название из WB', 7777)

    prod = _get(nm)
    assert prod.name == 'Название из WB'          # после снятия синк обновляет поле
    assert prod.price == 7777


def test_unpin_all_fields(admin_client):
    nm = _seed()
    admin_client.post('/admin/api/product-update',
                      json={'source': 'wb', 'product_id': nm,
                            'name': 'X', 'price': 1234}, headers=_csrf())
    assert set(_get(nm).manual_overrides) == {'name', 'price'}
    r = admin_client.post('/admin/api/product-unpin',
                          json={'source': 'wb', 'product_id': nm}, headers=_csrf())
    assert r.get_json()['success']
    assert _get(nm).manual_overrides is None


def test_update_without_changes_pins_nothing(admin_client):
    nm = _seed()
    prod = _get(nm)
    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'product_id': nm,
                                'name': prod.name, 'price': prod.price,
                                'stock': prod.stock}, headers=_csrf())
    assert r.get_json()['success']
    assert r.get_json()['manual_overrides'] == []  # значения не менялись — не закрепляем


def test_product_create_with_all_fields(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={
        'name': 'Свой товар полный', 'description': 'Описание своего',
        'price': 3000, 'discount_type': 'percent', 'discount_value': 5,
        'photo': '/static/uploads/x.jpg', 'category': 'Подушки',
        'stock': 9, 'visible': True, 'site_section': 'namatrasniki',
        'brand': 'СвойБренд', 'vendor_code': 'SKU-42',
        'url': 'https://example.com/item', 'rating': 4.7, 'review_count': 13,
        'characteristics': {'Материал': 'Хлопок', 'Размер': '90х200'},
        'seo_title': 'SEO заголовок', 'seo_description': 'SEO описание',
    }, headers=_csrf())
    body = r.get_json()
    assert body['success'] and body['url'].startswith('/catalog/site/')
    prod = db.session.query(Product).filter_by(
        source='site', nm_id=body['product_id']).first()
    assert prod is not None
    assert prod.vendor_code == 'SKU-42' and prod.rating == 4.7
    assert prod.review_count == 13 and prod.site_section == 'namatrasniki'
    assert prod.extra['Материал'] == 'Хлопок' and prod.extra['Размер'] == '90х200'
    assert prod.extra['seo_title'] == 'SEO заголовок'
    assert prod.extra['seo_description'] == 'SEO описание'
    assert prod.extra['description'] == 'Описание своего'


def test_product_update_each_field(admin_client):
    nm = _seed()
    fields = {
        'brand': 'НовыйБренд',
        'vendor_code': 'ART-7',
        'url': 'https://example.com/new',
        'rating': 4.9,
        'review_count': 55,
        'site_section': 'namatrasniki',
        'characteristics': {'Цвет': 'серый'},
        'seo_title': 'Заголовок',
        'seo_description': 'Описание',
    }
    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'product_id': nm, **fields},
                          headers=_csrf())
    assert r.get_json()['success']

    prod = _get(nm)
    assert prod.brand == 'НовыйБренд'
    assert prod.vendor_code == 'ART-7'
    assert prod.url == 'https://example.com/new'
    assert prod.rating == 4.9
    assert prod.review_count == 55
    assert prod.site_section == 'namatrasniki'
    assert prod.extra['Цвет'] == 'серый'
    assert prod.extra['seo_title'] == 'Заголовок'
    assert prod.extra['seo_description'] == 'Описание'

    # перезапись характеристик не трёт описание и SEO
    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'product_id': nm,
                                'characteristics': {'Материал': 'Лён'}},
                          headers=_csrf())
    assert r.get_json()['success']
    prod = _get(nm)
    assert prod.extra['Материал'] == 'Лён'
    assert 'Цвет' not in prod.extra
    assert prod.extra['seo_title'] == 'Заголовок'

    # закрепились только поля, которые синк WB перезаписывает
    assert set(prod.manual_overrides) == {'brand', 'vendor_code', 'url'}


def test_pins_survive_demo_sync(admin_client):
    nm = _seed()
    admin_client.post('/admin/api/product-update',
                      json={'source': 'wb', 'product_id': nm, 'stock': 3},
                      headers=_csrf())
    wb_sync.sync(token=None, demo=True)  # полный синк (как по кнопке в админке)
    prod = _get(nm)
    assert prod.stock == 3                      # закреплённый остаток не затирается
    assert 'stock' in (prod.manual_overrides or [])

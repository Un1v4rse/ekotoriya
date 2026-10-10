# -*- coding: utf-8 -*-
"""Создание товаров в админке (замечание «своя страница + продажа на сайте»).

POST /admin/api/product-create создаёт товар source='site' (nm_id=id),
товар появляется в общем каталоге (get_products/_filtered_catalog), имеет
свою страницу /catalog/site/<id>/ с кнопками покупки; редактируется через
существующий product-update, удаляется через product-delete. WB/Ozon-синки
site-товары не трогают (в синках фильтр source='wb'/'ozon').
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


def test_product_create_minimal(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={
        'name': 'Тестовый товар сайта', 'price': 1500, 'stock': 4,
    }, headers=_csrf())
    assert r.get_json()['success']
    pid = r.get_json()['product_id']
    prod = db.session.query(Product).filter_by(source='site', nm_id=pid).first()
    assert prod is not None and prod.name == 'Тестовый товар сайта'
    assert prod.price == 1500 and prod.stock == 4
    assert prod.visible is True


def test_product_create_full_fields_and_page(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={
        'name': 'Наматрасник тестовый 90х200 серый',
        'description': 'Описание <b>своего</b> товара.',
        'price': 2000, 'discount_type': 'percent', 'discount_value': 10,
        'photo': '/static/uploads/test.jpg',
        'category': 'Наматрасники', 'stock': 7, 'visible': True,
    }, headers=_csrf())
    body = r.get_json()
    assert body['success'] and body['url'].startswith('/catalog/site/')
    pid = body['product_id']

    # товар в общем каталоге
    app_module.invalidate_site_cache()
    view = app_module._filtered_catalog({})
    row = next(p for p in view['products'] if p.get('source') == 'site'
               and p.get('id') == pid)
    assert row['final_price'] == 1800  # 2000 - 10%
    assert row['extra']['description'] == 'Описание своего товара.'

    # своя страница с покупкой
    r = admin_client.get(f'/catalog/site/{pid}/')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    assert 'Наматрасник тестовый 90х200 серый' in html
    assert 'wb-cart-btn' in html and 'wb-buy-btn' in html and 'wb-like-btn' in html
    assert 'Описание своего товара.' in html
    assert '/catalog/site/' in html  # canonical на свою страницу

    # фильтры каталога видят товар (размер из имени, цвет, поиск)
    assert app_module._filtered_catalog({'size': '90x200'})['total'] >= 1
    assert app_module._filtered_catalog({'color': 'серый'})['total'] == 1
    assert app_module._filtered_catalog({'q': 'тестовый'})['total'] == 1
    assert app_module._filtered_catalog({'source': 'site'})['total'] == 1

    # карточка каталога ссылается на свою страницу
    r = admin_client.get('/catalog/?source=site')
    assert f'href="/catalog/site/{pid}/"' in r.data.decode('utf-8')


def test_product_create_validation(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={'name': ''},
                          headers=_csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/product-create',
                          json={'name': 'x', 'price': 'не число'}, headers=_csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/product-create',
                          json={'name': 'x', 'discount_type': 'bogus'},
                          headers=_csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/product-create',
                          json={'name': 'x', 'photo': 'javascript:alert(1)'},
                          headers=_csrf())
    assert r.status_code == 400


def test_product_create_auth_required():
    _seed()
    client = app_module.app.test_client()
    r = client.post('/admin/api/product-create', json={'name': 'x'})
    assert r.status_code in (401, 403)


def test_product_update_and_delete_site_product(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={
        'name': 'Товар на удаление', 'price': 900,
    }, headers=_csrf())
    pid = r.get_json()['product_id']

    # редактирование через общий product-update (source+nm_id)
    r = admin_client.post('/admin/api/product-update', json={
        'source': 'site', 'nm_id': pid, 'name': 'Переименованный',
        'price': 800, 'stock': 2, 'visible': False,
    }, headers=_csrf())
    assert r.get_json()['success']
    prod = db.session.query(Product).filter_by(source='site', nm_id=pid).first()
    assert prod.name == 'Переименованный' and prod.visible is False

    # скрыт → на сайте 404
    app_module.invalidate_site_cache()
    assert admin_client.get(f'/catalog/site/{pid}/').status_code == 404

    # удаление
    r = admin_client.post('/admin/api/product-delete', json={
        'source': 'site', 'product_id': pid}, headers=_csrf())
    assert r.get_json()['success']
    assert db.session.query(Product).filter_by(source='site', nm_id=pid).first() is None


def test_product_delete_not_found(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-delete', json={
        'source': 'site', 'product_id': 424242}, headers=_csrf())
    assert r.status_code == 404


def test_wb_sync_does_not_touch_site_products(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={
        'name': 'Свой товар', 'price': 1000,
    }, headers=_csrf())
    pid = r.get_json()['product_id']
    # повторная (демо-)синхронизация WB
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    prod = db.session.query(Product).filter_by(source='site', nm_id=pid).first()
    assert prod is not None and prod.name == 'Свой товар'
    view = app_module._filtered_catalog({'source': 'site'})
    assert view['total'] == 1


def test_recommendations_card_links_internal():
    """Карточки рекомендаций ссылаются на свою страницу, а не на маркетплейс."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader('templates'))
    tpl = env.get_template('recommendations_block_inner.html')
    html = tpl.render(recommended_products=[{
        'product': {'id': 890129778, 'source': 'wb', 'name': 'Тест',
                    'photo': '/static/x.jpg', 'price': 100, 'final_price': 90,
                    'admin_discount_percent': 0,
                    'url': 'https://www.wildberries.ru/catalog/890129778/detail.aspx'},
        'orders': 1, 'likes': 2, 'score': 1,
    }])
    assert 'href="/catalog/wb/890129778/"' in html
    assert 'wildberries.ru' not in html
    assert 'target="_blank"' not in html

# -*- coding: utf-8 -*-
"""Админ API: скидки, правила, фильтр выгрузки, аудит (PR-3/PR-7, WB-5/OZ-4, TS-2)."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import wb_sync
from db import db
from models import AuditLog, Discount, Product


@pytest.fixture()
def admin_client():
    """Авторизованный test_client с CSRF-токеном."""
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


def _csrf(client):
    return {'X-CSRF-Token': 'test-csrf', 'Content-Type': 'application/json'}


def test_admin_products_include_hidden():
    wb_sync.sync(token=None, demo=True)
    client = app_module.app.test_client()
    assert client.get('/admin/api/wb-products').status_code == 401  # без auth
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin', 'hash': generate_password_hash('testpass')})
    client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    data = client.get('/admin/api/wb-products').get_json()
    assert data['success'] and len(data['products']) >= 8
    assert all('visible' in p for p in data['products'])


def test_product_discount_percent_and_amount(admin_client):
    wb_sync.sync(token=None, demo=True)
    nm = wb_sync.load_products()[0]['nm_id']
    r = admin_client.post('/admin/api/product-discount',
                          json={'source': 'wb', 'product_id': nm,
                                'discount_type': 'percent', 'value': 15}, headers=_csrf(admin_client))
    assert r.get_json()['success']
    p = next(p for p in wb_sync.load_products() if p['nm_id'] == nm)
    assert p['admin_discount_percent'] == 15

    r = admin_client.post('/admin/api/product-discount',
                          json={'source': 'wb', 'product_id': nm,
                                'discount_type': 'amount', 'value': 300}, headers=_csrf(admin_client))
    assert r.get_json()['success']
    p = next(p for p in wb_sync.load_products() if p['nm_id'] == nm)
    assert p['admin_discount_type'] == 'amount' and p['admin_discount_amount'] == 300
    assert p['admin_discount_percent'] == 0  # процент сброшен


def test_discount_bulk_selected(admin_client):
    wb_sync.sync(token=None, demo=True)
    products = wb_sync.load_products()
    ids = [p['nm_id'] for p in products[:3]]
    r = admin_client.post('/admin/api/discount-bulk',
                          json={'scope': 'selected',
                                'items': [{'source': 'wb', 'product_id': i} for i in ids],
                                'discount_type': 'percent', 'value': 20},
                          headers=_csrf(admin_client))
    assert r.get_json()['success']
    updated = {p['nm_id']: p for p in wb_sync.load_products()}
    assert all(updated[i]['admin_discount_percent'] == 20 for i in ids)


def test_discount_rules_crud_and_audit(admin_client):
    r = admin_client.post('/admin/api/discount-bulk',
                          json={'scope': 'all', 'discount_type': 'percent', 'value': 10},
                          headers=_csrf(admin_client))
    assert r.get_json()['success']
    rules = admin_client.get('/admin/api/discount-rules').get_json()['rules']
    assert any(x['scope'] == 'all' and x['value'] == 10 for x in rules)

    # сработала на цену
    import pricing
    assert pricing.final_price({'price': 1000}) == 900

    # удаление
    rule_id = next(x['id'] for x in rules if x['scope'] == 'all')
    r = admin_client.post('/admin/api/discount-rule/delete', json={'id': rule_id},
                          headers=_csrf(admin_client))
    assert r.get_json()['success']
    rules = admin_client.get('/admin/api/discount-rules').get_json()['rules']
    assert all(x['id'] != rule_id for x in rules)

    # аудит (PR-7)
    entries = db.session.query(AuditLog).all()
    actions = {e.action for e in entries}
    assert 'discount.rule' in actions and 'discount.rule_delete' in actions
    assert all(e.admin_user == 'testadmin' for e in entries)


def test_export_settings_roundtrip(admin_client):
    wb_sync.sync(token=None, demo=True)
    nm = wb_sync.load_products()[0]['nm_id']
    r = admin_client.post('/admin/api/export-settings',
                          json={'source': 'wb', 'mode': 'selected',
                                'selected_ids': [nm], 'category': ''},
                          headers=_csrf(admin_client))
    assert r.get_json()['success']
    settings = admin_client.get('/admin/api/export-settings?source=wb').get_json()['settings']
    assert settings['mode'] == 'selected' and settings['selected_ids'] == [nm]
    # видимость применилась
    app_module.invalidate_site_cache()
    visible_ids = [p['nm_id'] for p in app_module.get_products()]
    assert visible_ids == [nm]


def test_csrf_required_on_admin_post(admin_client):
    wb_sync.sync(token=None, demo=True)
    nm = wb_sync.load_products()[0]['nm_id']
    r = admin_client.post('/admin/api/product-discount',
                          json={'source': 'wb', 'product_id': nm,
                                'discount_type': 'percent', 'value': 50})
    assert r.status_code == 403


# --- /admin/api/product-update (управление товарами) ---

def test_product_update_all_fields(admin_client):
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    nm = wb_sync.load_products()[0]['nm_id']

    r = admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'nm_id': nm,
        'name': 'Новое имя товара <b>с тегом</b>',
        'description': 'Описание товара.<br>Вторая строка.',
        'price': 5555,
        'discount_type': 'amount', 'discount_value': 500,
        'photo': 'https://example.com/photo.jpg',
        'category': 'Наматрасники',
        'visible': False,
    }, headers=_csrf(admin_client))
    assert r.get_json()['success']

    p = next(p for p in wb_sync.load_products() if p['nm_id'] == nm)
    assert p['name'] == 'Новое имя товара <b>с тегом</b>'
    assert p['price'] == 5555
    assert p['admin_discount_type'] == 'amount' and p['admin_discount_amount'] == 500
    assert p['admin_discount_percent'] == 0
    assert p['photo'] == 'https://example.com/photo.jpg'
    assert p['category'] == 'Наматрасники'
    assert p['visible'] is False
    # описание хранится в extra (HTML-теги вырезаются) и отдаётся наружу
    assert p['extra'].get('description') == 'Описание товара.Вторая строка.'

    api = admin_client.get('/admin/api/wb-products').get_json()
    row = next(x for x in api['products'] if x['nm_id'] == nm)
    assert row['name'] == p['name'] and row['description'] == 'Описание товара.Вторая строка.'
    assert row['visible'] is False


def test_product_update_visible_on_site_page(admin_client):
    wb_sync.sync(token=None, demo=True)
    nm = 890129778
    admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'nm_id': nm,
        'name': 'Товар для проверки страницы',
        'description': 'Описание для страницы товара.',
        'price': 4321,
        'discount_type': 'percent', 'discount_value': 10,
    }, headers=_csrf(admin_client))
    r = admin_client.get(f'/catalog/wb/{nm}/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'Товар для проверки страницы' in body
    assert 'Описание для страницы товара.' in body
    assert '4321' in body


def test_product_update_auth_required():
    wb_sync.sync(token=None, demo=True)
    client = app_module.app.test_client()
    r = client.post('/admin/api/product-update',
                    json={'source': 'wb', 'nm_id': 1, 'name': 'x'})
    assert r.status_code in (401, 403)  # CSRF (403) срабатывает раньше admin_required (401)


def test_product_update_not_found(admin_client):
    wb_sync.sync(token=None, demo=True)
    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'nm_id': 999999999, 'name': 'x'},
                          headers=_csrf(admin_client))
    assert r.status_code == 404


def test_product_update_validation_errors(admin_client):
    wb_sync.sync(token=None, demo=True)
    nm = wb_sync.load_products()[0]['nm_id']

    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'nm_id': nm,
                                'discount_type': 'bogus', 'discount_value': 10},
                          headers=_csrf(admin_client))
    assert r.status_code == 400

    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'nm_id': nm,
                                'discount_type': 'percent', 'discount_value': 'abc'},
                          headers=_csrf(admin_client))
    assert r.status_code == 400

    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'nm_id': nm,
                                'photo': 'javascript:alert(1)'},
                          headers=_csrf(admin_client))
    assert r.status_code == 400

    r = admin_client.post('/admin/api/product-update',
                          json={'source': 'wb', 'nm_id': nm, 'price': 'не число'},
                          headers=_csrf(admin_client))
    assert r.status_code == 400

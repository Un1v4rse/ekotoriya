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

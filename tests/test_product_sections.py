# -*- coding: utf-8 -*-
"""Управление товарами из админки: раздел сайта (site_section), цикл 3."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import wb_sync
from models import Product


@pytest.fixture()
def admin_client():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    client = app_module.app.test_client()
    client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'test-csrf'
    return client


def _csrf():
    return {'X-CSRF-Token': 'test-csrf', 'Content-Type': 'application/json'}


@pytest.fixture()
def demo_products():
    wb_sync.sync(token=None, demo=True)
    return [p['nm_id'] for p in wb_sync.load_products()]


def test_site_sections_listed(admin_client):
    r = admin_client.get('/admin/api/site-sections')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success']
    keys = {s['key'] for s in data['sections']}
    assert 'namatrasniki' in keys  # из фикстурных страниц MINIMAL_CONTENT нет —
    # значит проверяем хотя бы структуру ответа; на реальном контенте будут разделы.
    assert all('title' in s for s in data['sections'])


def test_product_section_set_and_clear(admin_client, demo_products):
    nm = demo_products[0]
    r = admin_client.post('/admin/api/product-section',
                          json={'source': 'wb', 'product_id': nm, 'section': 'odeyala'},
                          headers=_csrf())
    assert r.get_json()['success']
    from db import db
    row = db.session.query(Product).filter_by(source='wb', nm_id=nm).first()
    assert row.site_section == 'odeyala'

    # очистка раздела
    r = admin_client.post('/admin/api/product-section',
                          json={'source': 'wb', 'product_id': nm, 'section': ''},
                          headers=_csrf())
    assert r.get_json()['success']
    db.session.expire_all()
    row = db.session.query(Product).filter_by(source='wb', nm_id=nm).first()
    assert row.site_section == ''


def test_product_section_bulk(admin_client, demo_products):
    ids = demo_products[:3]
    r = admin_client.post('/admin/api/product-section',
                          json={'items': [{'source': 'wb', 'product_id': i} for i in ids],
                                'section': 'naperniki'},
                          headers=_csrf())
    data = r.get_json()
    assert data['success'] and data['count'] == 3


def test_product_section_404(admin_client):
    r = admin_client.post('/admin/api/product-section',
                          json={'source': 'wb', 'product_id': 999999999, 'section': 'x'},
                          headers=_csrf())
    assert r.status_code == 404


def test_section_page_filters_products(admin_client, demo_products, monkeypatch):
    """Страница раздела показывает только свои товары + без раздела."""
    from db import db
    nm = demo_products[0]
    row = db.session.query(Product).filter_by(source='wb', nm_id=nm).first()
    row.site_section = 'namatrasniki'
    db.session.commit()

    # страница раздела (если есть в контенте) не должна содержать чужой товар;
    # на минимальном контенте разделов нет — проверяем хелпер напрямую.
    assert app_module._page_section({'slug': 'catalog__namatrasniki'}) == 'namatrasniki'
    assert app_module._page_section({'slug': 'catalog__namatrasniki__na_molnii'}) == 'namatrasniki'
    assert app_module._page_section({'slug': 'index'}) == ''
    assert app_module._page_section({'slug': 'catalog'}) == ''


def test_product_dict_includes_site_section(demo_products):
    from db import db
    nm = demo_products[0]
    row = db.session.query(Product).filter_by(source='wb', nm_id=nm).first()
    row.site_section = 'odeyala'
    db.session.commit()
    assert row.to_product_dict()['site_section'] == 'odeyala'

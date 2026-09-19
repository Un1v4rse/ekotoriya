# -*- coding: utf-8 -*-
"""Клиенты: сегменты (CL-5), экспорт CSV (CL-4), согласие ПДн и удаление (CL-6)."""
import csv
import io
from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import customers
from db import db
from models import Customer, Event, Order


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


PRODUCTS = [
    {'nm_id': 101, 'name': 'Наматрасник А', 'category': 'Наматрасники', 'source': 'wb'},
    {'nm_id': 103, 'name': 'Подушка', 'category': 'Подушки', 'source': 'wb'},
]


def test_segments_category_frequent_inactive():
    # категорийный покупатель
    c1 = customers.add_customer('Категория', '+7 900 200-00-01')
    customers.add_order('Категория', '+7 900 200-00-01', '', '101', 'Наматрасник А', 1000)
    # частый покупатель: 2 заявки за период
    c2 = customers.add_customer('Частый', '+7 900 200-00-02')
    customers.add_order('Частый', '+7 900 200-00-02', '', '101', 'Наматрасник А', 1000)
    customers.add_order('Частый', '+7 900 200-00-02', '', '103', 'Подушка', 900)
    # неактивный: заявка 60 дней назад
    c3 = customers.add_customer('Старый', '+7 900 200-00-03')
    customers.add_order('Старый', '+7 900 200-00-03', '', '103', 'Подушка', 900)
    old = db.session.query(Order).filter_by(customer_id=c3['id']).first()
    old.created_at = old.created_at - timedelta(days=60)
    db.session.commit()

    segments = {s['key']: s for s in customers.compute_segments(PRODUCTS, days=30, min_orders=2)}
    cat = segments['category:Наматрасники']
    assert cat['count'] == 2
    assert {c['id'] for c in cat['customers']} == {c1['id'], c2['id']}
    freq = segments['orders>=2:30d']
    assert freq['count'] == 1 and freq['customers'][0]['id'] == c2['id']
    inact = segments['inactive:30d']
    assert inact['count'] == 1 and inact['customers'][0]['id'] == c3['id']


def test_segments_admin_api(admin_client):
    customers.add_order('Апи', '+7 900 200-00-04', '', '101', 'Наматрасник А', 1000)
    r = admin_client.get('/admin/api/segments?days=30&min_orders=1')
    data = r.get_json()
    assert r.status_code == 200 and data['success']
    assert any(s['count'] >= 1 for s in data['segments'])


def test_customers_csv_export(admin_client):
    customers.add_order('Экспорт', '+7 900 200-00-05', 'exp@example.com',
                        '101', 'Наматрасник А', 1500, qty=2, consent_pd=True)
    r = admin_client.get('/admin/api/customers/export')
    assert r.status_code == 200
    assert 'text/csv' in r.headers.get('Content-Type', '')
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    text = r.data.decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(text), delimiter=';'))
    assert rows[0][:5] == ['id', 'name', 'phone', 'email', 'consent_pd']
    assert len(rows) == 2
    assert rows[1][1] == 'Экспорт'
    assert rows[1][4] == 'да'
    assert rows[1][6] == '3000'  # total_spent = 1500 × 2


def test_consent_pd_via_public_forms():
    client = app_module.app.test_client()
    r = client.post('/api/register', json={
        'name': 'Согласный', 'phone': '+7 900 200-00-06', 'consent_pd': True})
    assert r.get_json()['success']
    c = customers.get_customer_by_phone('+79002000006')
    assert c['consent_pd'] is True
    r = client.post('/api/order', json={
        'name': 'Согласный', 'phone': '+7 900 200-00-06', 'product_id': '101',
        'product_name': 'Наматрасник А', 'price': 1000, 'consent_pd': True})
    assert r.get_json()['success']
    c = customers.get_customer_by_phone('+79002000006')
    assert c['consent_pd'] is True


def test_delete_customer_cascade(admin_client):
    c = customers.add_customer('На удаление', '+7 900 200-00-07')
    customers.add_order('На удаление', '+7 900 200-00-07', '', '101', 'Наматрасник А', 1000)
    customers.add_like('101', 'Наматрасник А', customer_id=c['id'])
    cid = c['id']
    r = admin_client.post('/admin/api/customer/delete', json={'id': cid}, headers=_csrf())
    assert r.get_json()['success']
    assert db.session.get(Customer, cid) is None
    assert db.session.query(Order).filter_by(customer_id=cid).count() == 0
    assert db.session.query(Event).filter_by(customer_id=cid).count() == 0
    r = admin_client.post('/admin/api/customer/delete', json={'id': cid}, headers=_csrf())
    assert r.status_code == 404


def test_customer_apis_require_auth():
    client = app_module.app.test_client()
    assert client.get('/admin/api/customers/export').status_code in (301, 302, 401)
    assert client.get('/admin/api/segments').status_code in (301, 302, 401)

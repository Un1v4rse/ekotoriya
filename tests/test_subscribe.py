# -*- coding: utf-8 -*-
"""Подписка на рассылку из подвала (subscribers).

Покрытие:
- успешная подписка: email в БД, сообщение «Вы подписаны на рассылку»;
- дубль: вторая запись не создаётся, ответ success с already=True;
- невалидный email -> 400;
- регистр email нормализуется (Lower) — дубль в другом регистре не проходит;
- админ-API: список подписчиков и удаление (с логином и без).
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from db import db
from models import Subscriber


@pytest.fixture()
def client():
    return app_module.app.test_client()


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


def _post(client, email, source='footer'):
    return client.post('/api/subscribe', json={'email': email, 'source': source})


def test_subscribe_success(client):
    resp = _post(client, 'user@example.com')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['already'] is False
    assert data['message'] == 'Вы подписаны на рассылку'
    subs = db.session.query(Subscriber).all()
    assert len(subs) == 1
    assert subs[0].email == 'user@example.com'
    assert subs[0].source == 'footer'


def test_subscribe_duplicate_no_second_row(client):
    assert _post(client, 'dupe@example.com').get_json()['success'] is True
    resp = _post(client, 'dupe@example.com')
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['success'] is True
    assert data['already'] is True
    assert db.session.query(Subscriber).count() == 1


def test_subscribe_case_insensitive_duplicate(client):
    assert _post(client, 'Case@Test.com').get_json()['success'] is True
    resp = _post(client, '  case@test.COM ')
    assert resp.get_json()['already'] is True
    assert db.session.query(Subscriber).count() == 1


def test_subscribe_invalid_email(client):
    for bad in ('not-an-email', 'a@b', 'a b@c.ru', '@x.ru', 'a@b.c', ''):
        resp = _post(client, bad)
        assert resp.status_code == 400, bad
        assert resp.get_json()['success'] is False
    assert db.session.query(Subscriber).count() == 0


def test_subscribe_source_sanitized(client):
    resp = _post(client, 's@example.com', source='foot<script>er x')
    assert resp.get_json()['success'] is True
    sub = db.session.query(Subscriber).first()
    assert '<' not in sub.source and '>' not in sub.source


def test_admin_subscribers_list_and_delete(admin_client, client):
    _post(client, 'one@example.com')
    _post(client, 'two@example.com')

    resp = admin_client.get('/admin/api/subscribers')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    emails = sorted(s['email'] for s in data['subscribers'])
    assert emails == ['one@example.com', 'two@example.com']
    assert all('created_at' in s and 'source' in s for s in data['subscribers'])

    sid = data['subscribers'][0]['id']
    resp = admin_client.post('/admin/api/subscriber/delete',
                             json={'id': sid},
                             headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    assert db.session.query(Subscriber).count() == 1


def test_admin_subscribers_requires_login(client):
    assert client.get('/admin/api/subscribers').status_code == 401
    resp = client.post('/admin/api/subscriber/delete', json={'id': 1})
    assert resp.status_code in (401, 403)  # без сессии/CSRF — любой отказ


def test_admin_subscriber_delete_validation(admin_client):
    resp = admin_client.post('/admin/api/subscriber/delete',
                             json={},
                             headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 400
    resp = admin_client.post('/admin/api/subscriber/delete',
                             json={'id': 9999},
                             headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.status_code == 404


def test_subscribe_form_rendered_in_footer(client):
    """В подвале страницы — рабочая форма подписки, а не мёртвая кнопка."""
    resp = client.get('/catalog/')
    html = resp.get_data(as_text=True)
    assert 'wb-subscribe-form' in html
    assert 'action="/api/subscribe"' in html
    assert 'data-event="jqm" data-param-id="subscribe"' not in html

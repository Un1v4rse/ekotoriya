# -*- coding: utf-8 -*-
"""Кнопки конструктора с действиями (цикл 4 / этап 5).

Покрытие:
- рендер блока «Кнопка» для действий link/anchor/tel/mailto/form/subscribe/popup;
- санитизация: javascript: не проходит, грязный якорь/телефон чистятся;
- попап: контент в hidden-div, HTML экранируется;
- /api/feedback: сохранение заявки, 400 без имени и телефона;
- админ-API заявок: список только с логином, удаление с аудитом.
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import blocks as blocks_module
from db import db
from models import FeedbackRequest


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


def _render(**block):
    block.setdefault('type', 'button')
    block.setdefault('text', 'Жми')
    return blocks_module.render_block(block)


def test_button_link_default():
    html = _render(url='/catalog/')
    assert 'href="/catalog/"' in html


def test_button_link_sanitizes_javascript():
    html = _render(url='javascript:alert(1)')
    assert 'javascript:' not in html
    assert 'href="#"' in html


def test_button_anchor():
    html = _render(action='anchor', anchor='about-us')
    assert 'href="#about-us"' in html
    # грязный якорь чистится
    html2 = _render(action='anchor', anchor='ab<script>out')
    assert '<script>' not in html2
    assert 'href="#abscriptout"' in html2


def test_button_tel():
    html = _render(action='tel', phone='+7 (900) 123-45-67')
    assert 'href="tel:+79001234567"' in html


def test_button_mailto():
    html = _render(action='mailto', email='info@example.com')
    assert 'href="mailto:info@example.com"' in html


def test_button_form_and_subscribe_actions():
    html = _render(action='form')
    assert 'data-wb-ctor-action="form"' in html
    html = _render(action='subscribe')
    assert 'data-wb-ctor-action="subscribe"' in html


def test_button_popup_hidden_content():
    html = _render(action='popup', popup_title='Скидки', popup_text='Привет\n<b>Мир</b>')
    assert 'data-wb-ctor-action="popup"' in html
    assert 'wb-ctor-popup-content' in html
    assert 'Скидки' in html
    # HTML в тексте экранируется
    assert '<b>Мир</b>' not in html
    assert '&lt;b&gt;Мир&lt;/b&gt;' in html


def test_button_unknown_action_falls_back_to_link():
    html = _render(action='hacker', url='/ok/')
    assert 'href="/ok/"' in html


def test_feedback_success(client):
    resp = client.post('/api/feedback', json={
        'name': 'Иван', 'phone': '+7 900 111-22-33', 'message': 'Перезвоните', 'page': '/contacts/'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    row = db.session.query(FeedbackRequest).one()
    assert row.name == 'Иван'
    assert row.phone == '+7 900 111-22-33'
    assert row.page == '/contacts/'


def test_feedback_requires_name_or_phone(client):
    resp = client.post('/api/feedback', json={'name': '', 'phone': '', 'message': 'x'})
    assert resp.status_code == 400
    assert db.session.query(FeedbackRequest).count() == 0


def test_feedback_phone_sanitized(client):
    client.post('/api/feedback', json={'name': 'Иван', 'phone': '<script>alert(1)</script>8900'})
    row = db.session.query(FeedbackRequest).one()
    assert '<script>' not in row.phone
    assert '8900' in row.phone


def test_admin_feedback_requires_login(client):
    assert client.get('/admin/api/feedback').status_code == 401


def test_admin_feedback_list_and_delete(admin_client):
    db.session.add(FeedbackRequest(name='Анна', phone='8900', message='x', page='/'))
    db.session.commit()
    resp = admin_client.get('/admin/api/feedback')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] and len(data['feedback']) == 1
    fid = data['feedback'][0]['id']

    resp = admin_client.post('/admin/api/feedback/delete', json={'id': fid},
                             headers={'X-CSRF-Token': 'test-csrf'})
    assert resp.get_json()['success'] is True
    assert db.session.query(FeedbackRequest).count() == 0


def test_admin_feedback_delete_validation(admin_client):
    assert admin_client.post('/admin/api/feedback/delete', json={},
                             headers={'X-CSRF-Token': 'test-csrf'}).status_code == 400
    assert admin_client.post('/admin/api/feedback/delete', json={'id': 9999},
                             headers={'X-CSRF-Token': 'test-csrf'}).status_code == 404

# -*- coding: utf-8 -*-
"""Расширение форм (Приоритет 2): кастомные поля, валидация, хонипот,
обратная совместимость со старой формой.
"""
import html
import json

import pytest

import app as app_module
import blocks as blocks_module
from db import db
from models import FeedbackRequest


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _render_form(**kwargs):
    block = {'type': 'form', 'title': 'Заказать звонок', 'button': 'Отправить'}
    block.update(kwargs)
    return blocks_module.render_block(block)


def test_form_without_fields_uses_legacy_order_endpoint():
    rendered = _render_form()
    assert 'action="/api/order"' in rendered
    assert 'name="name"' in rendered
    assert 'name="phone"' in rendered
    assert 'action="/api/form"' not in rendered


def test_form_with_custom_fields_renders_new_endpoint():
    rendered = _render_form(fields=[
        {'name': 'name', 'label': 'Ваше имя', 'type': 'text', 'required': True},
        {'name': 'phone', 'label': 'Телефон', 'type': 'tel', 'required': True},
        {'name': 'email', 'label': 'Email', 'type': 'email', 'placeholder': 'name@example.com'},
    ])
    assert 'action="/api/form"' in rendered
    assert 'name="form_schema"' in rendered
    assert 'name="website"' in rendered  # honeypot
    assert 'name="name"' in rendered
    assert 'name="phone"' in rendered
    assert 'name="email"' in rendered
    # schema JSON в hidden input экранирован через esc(); раскодируем для проверки
    schema_raw = rendered.split('name="form_schema" value="')[1].split('"')[0]
    schema = json.loads(html.unescape(schema_raw))
    assert schema[0]['name'] == 'name'


def test_form_select_renders_options():
    rendered = _render_form(fields=[
        {'name': 'city', 'label': 'Город', 'type': 'select', 'options': ['Москва', 'СПб']},
    ])
    assert '<select name="city"' in rendered
    assert '<option value="Москва">Москва</option>' in rendered
    assert '<option value="СПб">СПб</option>' in rendered


def test_form_checkbox_renders():
    rendered = _render_form(fields=[
        {'name': 'agree', 'label': 'Согласен', 'type': 'checkbox', 'required': True},
    ])
    assert 'type="checkbox" name="agree"' in rendered
    assert 'Согласен' in rendered


def test_api_form_success_creates_feedback(client):
    schema = json.dumps([
        {'name': 'name', 'type': 'text', 'required': True, 'label': 'Имя'},
        {'name': 'phone', 'type': 'tel', 'required': True, 'label': 'Телефон'},
        {'name': 'email', 'type': 'email', 'required': False, 'label': 'Email'},
    ])
    resp = client.post('/api/form', data={
        'form_title': 'Тестовая форма',
        'form_schema': schema,
        'name': 'Иван',
        'phone': '+7 900 111-22-33',
        'email': 'ivan@example.com',
    })
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    row = db.session.query(FeedbackRequest).one()
    assert row.name == 'Иван'
    assert row.phone == '+7 900 111-22-33'
    stored = json.loads(row.message)
    assert stored['values']['email'] == 'ivan@example.com'


def test_api_form_missing_required_returns_error(client):
    schema = json.dumps([{'name': 'name', 'type': 'text', 'required': True, 'label': 'Имя'}])
    resp = client.post('/api/form', data={'form_schema': schema})
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False
    assert db.session.query(FeedbackRequest).count() == 0


def test_api_form_honeypot_silently_rejects(client):
    schema = json.dumps([{'name': 'name', 'type': 'text', 'required': True, 'label': 'Имя'}])
    resp = client.post('/api/form', data={
        'form_schema': schema, 'name': 'Бот', 'website': 'http://spam.example'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    assert db.session.query(FeedbackRequest).count() == 0


def test_api_form_redirect_success(client):
    schema = json.dumps([{'name': 'name', 'type': 'text', 'required': True, 'label': 'Имя'}])
    resp = client.post('/api/form', data={
        'form_schema': schema, 'name': 'Иван', 'redirect': '1'},
        headers={'Referer': '/contacts/'})
    assert resp.status_code == 302
    assert '/contacts/?form=ok' in resp.location

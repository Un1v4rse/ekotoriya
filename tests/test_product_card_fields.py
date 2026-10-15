# -*- coding: utf-8 -*-
"""Цикл 4, этап 2: новые редактируемые поля карточки товара.

Состав (composition), уход (care), размерная сетка (size_chart), видео
(video_url) и бейджи «Хит»/«Новинка» (badges) — админские правки в
extra товара: хранятся отдельно от данных API, приоритетны при рендере,
пустое значение = удаление правки (блок исчезает с карточки).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as app_module
from werkzeug.security import generate_password_hash


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


def _make_product(client, name='Тестовый наматрасник 90x200'):
    r = client.post('/admin/api/product-create', headers=_csrf(), json={
        'name': name, 'price': 1000, 'photo': '/static/x.png',
        'composition': 'верх — микрофибра',
        'care': 'стирка при 30°C',
        'size_chart': '90x200 — борт до 25 см',
        'video_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'badges': ['hit', 'new', 'unknown-badge'],
    })
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    return r.get_json()['product_id']


def test_create_with_new_fields(admin_client):
    pid = _make_product(admin_client)
    r = admin_client.get(f'/catalog/site/{pid}/')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # бейджи «Хит»/«Новинка» на фото (unknown-badge отброшен)
    assert 'Хит' in html and 'Новинка' in html
    assert 'unknown-badge' not in html
    # состав/уход/размерная сетка — блоками под описанием
    assert 'Состав' in html and 'микрофибра' in html
    assert 'Уход' in html and 'стирка при 30' in html
    assert 'Размерная сетка' in html and 'борт до 25 см' in html
    # youtube-ссылка превращена в embed
    assert 'youtube.com/embed/dQw4w9WgXcQ' in html


def test_update_and_clear_new_fields(admin_client):
    pid = _make_product(admin_client)
    r = admin_client.post('/admin/api/product-update', headers=_csrf(), json={
        'source': 'site', 'product_id': pid,
        'composition': '', 'care': '', 'size_chart': '',
        'video_url': '', 'badges': [],
    })
    assert r.status_code == 200 and r.get_json()['success']
    html = admin_client.get(f'/catalog/site/{pid}/').get_data(as_text=True)
    # правки удалены — блоки исчезли (возврат к дефолту карточки)
    assert 'Состав' not in html
    assert 'Уход' not in html
    assert 'Размерная сетка' not in html
    assert 'youtube.com/embed' not in html
    assert '>Хит<' not in html


def test_update_badges_and_video_validation(admin_client):
    pid = _make_product(admin_client)
    r = admin_client.post('/admin/api/product-update', headers=_csrf(), json={
        'source': 'site', 'product_id': pid, 'video_url': 'javascript:alert(1)'})
    assert r.status_code == 400
    r = admin_client.post('/admin/api/product-update', headers=_csrf(), json={
        'source': 'site', 'product_id': pid, 'badges': 'not-a-list'})
    assert r.status_code == 400
    r = admin_client.post('/admin/api/product-update', headers=_csrf(), json={
        'source': 'site', 'product_id': pid, 'badges': ['hit']})
    assert r.status_code == 200
    html = admin_client.get(f'/catalog/site/{pid}/').get_data(as_text=True)
    assert '>Хит<' in html and 'Новинка' not in html


def test_characteristics_cleanup_keeps_new_fields(admin_client):
    """Сохранение характеристик не должно затирать состав/уход/бейджи."""
    pid = _make_product(admin_client)
    r = admin_client.post('/admin/api/product-update', headers=_csrf(), json={
        'source': 'site', 'product_id': pid,
        'characteristics': {'Материал': 'Микрофибра'}})
    assert r.status_code == 200
    html = admin_client.get(f'/catalog/site/{pid}/').get_data(as_text=True)
    assert 'Материал' in html
    assert 'Состав' in html and 'микрофибра' in html
    assert '>Хит<' in html

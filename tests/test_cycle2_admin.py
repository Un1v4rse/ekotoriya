# -*- coding: utf-8 -*-
"""Цикл 2 (UI-разделы админки): CN-T6 (глобальные зоны), CN-T7 (предпросмотр темы),
CN-T9 (шаблоны страниц / мои блоки) + рендер admin.html с новыми секциями."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module


@pytest.fixture()
def admin_client():
    """Суперадмин через формальный логин (паттерн test_cycle2_server.py)."""
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    c = app_module.app.test_client()
    c.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    with c.session_transaction() as s:
        s['csrf_token'] = 'csrf'
    return c


def csrf():
    return {'X-CSRF-Token': 'csrf', 'Content-Type': 'application/json'}


# ---------- CN-T7.4: живой предпросмотр темы (без сохранения) ----------

def test_theme_preview_valid_theme(admin_client):
    theme = {'primary_color': '#112233', 'h1_size': '3.4rem',
             'heading_font_google': 'Roboto', 'button_variant': 'outline'}
    r = admin_client.post('/admin/api/theme-preview', json={'theme': theme}, headers=csrf())
    assert r.status_code == 200
    data = r.get_json()
    assert data['success']
    css = data['css']
    assert '--theme-primary: #112233' in css
    assert '--theme-h1: 3.4rem' in css
    assert '--theme-button-variant: outline' in css
    assert 'fonts.googleapis.com' in css  # Roboto из вайтлиста → @import
    # предпросмотр НЕ сохраняет тему
    current = admin_client.get('/admin/api/theme').get_json()['theme']
    assert current.get('primary_color') != '#112233'


def test_theme_preview_invalid_theme(admin_client):
    # не dict → 400
    r = admin_client.post('/admin/api/theme-preview', json={'theme': 'bad'}, headers=csrf())
    assert r.status_code == 400
    # пустой dict — нечего рендерить → 400
    r = admin_client.post('/admin/api/theme-preview', json={'theme': {}}, headers=csrf())
    assert r.status_code == 400
    # чужой шрифт и инъекция безопасно фолбэкаются, CSS валиден
    r = admin_client.post('/admin/api/theme-preview', json={'theme': {
        'heading_font_google': 'Evil"};body{display:none}', 'primary_color': 'red-not-hex'}},
        headers=csrf())
    assert r.status_code == 200
    css = r.get_json()['css']
    assert 'Evil' not in css
    assert 'body{display:none}' not in css
    assert '--theme-primary' in css


# ---------- CN-T6: глобальные зоны ----------

def test_global_zones_structure(admin_client):
    r = admin_client.get('/admin/api/global-zones')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success']
    header, footer = data['header'], data['footer']
    for key in ('logo_image', 'logo_text', 'menu', 'phone', 'email', 'cta',
                'socials', 'sticky', 'transparent', 'height', 'use_global'):
        assert key in header, key
    for key in ('columns', 'phone', 'email', 'address', 'socials',
                'copyright', 'badges', 'use_global'):
        assert key in footer, key
    assert isinstance(header['menu'], list)
    assert isinstance(header['cta'], dict)
    assert isinstance(footer['columns'], list)


def test_global_zones_save_and_reload(admin_client):
    r = admin_client.post('/admin/api/global-zones', json={
        'header': {'use_global': True, 'height': 90, 'logo_text': 'ТестШоп',
                   'menu': [{'title': 'Каталог', 'url': '/catalog/',
                             'children': [{'title': 'Все', 'url': '/catalog/all/'}]}]},
        'footer': {'use_global': True, 'copyright': 'ТестШоп 2025',
                   'columns': [{'title': 'Меню', 'links': [{'title': 'Главная', 'url': '/'}]}]},
    }, headers=csrf())
    assert r.status_code == 200
    assert r.get_json()['success']

    data = admin_client.get('/admin/api/global-zones').get_json()
    assert data['header']['use_global'] is True
    assert data['header']['height'] == 90
    assert data['header']['logo_text'] == 'ТестШоп'
    assert data['header']['menu'][0]['children'][0]['title'] == 'Все'
    assert data['footer']['copyright'] == 'ТестШоп 2025'
    assert data['footer']['columns'][0]['links'][0]['url'] == '/'


# ---------- Рендер admin.html с новыми секциями ----------

def test_admin_page_renders_cycle2_sections(admin_client):
    r = admin_client.get('/admin')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Шапка и подвал' in html
    assert 'Шаблоны</button>' in html
    assert 'tab-zones' in html
    assert 'tab-templates' in html
    assert 'Расширенная тема' in html
    assert 'theme-preview' in html  # endpoint предпросмотра задействован в JS

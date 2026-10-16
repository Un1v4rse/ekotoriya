# -*- coding: utf-8 -*-
"""Цикл 4, этап 3: страница «Контакты» — чистый email-якорь и Яндекс.Карта.

Карта инжектится в рендер (маркер wb-contacts-map, идемпотентно), адрес для
поиска — из настройки site.map_query (дефолт: Череповец, ул. Гоголя 56).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
from werkzeug.security import generate_password_hash

BROKEN_EMAIL = (
    '<a href=\'mailto:&lt;a href="mailto:HelloEkotoriya@yandex.ru"&gt;'
    'helloekotoriya@yandex.ru&lt;/a&gt;\'>'
    '<a href="mailto:HelloEkotoriya@yandex.ru">helloekotoriya@yandex.ru</a></a>')
DEFAULT_SITE = {'phone': '8 800 201 08 24',
                'email': 'helloekotoriya@yandex.ru',
                'address': 'г. Череповец, ул. Гоголя 56'}


def test_broken_email_anchor_fixed():
    out = app_module._sync_contacts_html(f'<div>{BROKEN_EMAIL}</div>', DEFAULT_SITE)
    # вложенный mailto с экранированным HTML исчез, остался один чистый якорь
    assert 'mailto:&lt;' not in out
    assert out.count('<a href="mailto:') == 1
    assert 'mailto:helloekotoriya@yandex.ru' in out


def test_map_injected_once_with_default_address():
    out = app_module._sync_contacts_html('<div>info</div>', DEFAULT_SITE)
    assert out.count('wb-contacts-map') == 1
    assert 'yandex.ru/map-widget/v1/' in out
    # дефолтный адрес закодирован в параметре text=
    assert '%D0%A7%D0%B5%D1%80%D0%B5%D0%BF%D0%BE%D0%B2%D0%B5%D1%86' in out  # Череповец
    # повторный прогон не плодит блоки
    again = app_module._sync_contacts_html(out, DEFAULT_SITE)
    assert again.count('wb-contacts-map') == 1


def test_map_query_from_site_settings():
    site = dict(DEFAULT_SITE, map_query='Вологда, улица Ленина, 1')
    out = app_module._sync_contacts_html('<div>info</div>', site)
    assert '%D0%92%D0%BE%D0%BB%D0%BE%D0%B3%D0%B4%D0%B0' in out  # Вологда


def test_map_query_sanitized():
    # кавычки/теги в настройке не должны ломать разметку
    site = dict(DEFAULT_SITE, map_query='"><script>alert(1)</script>')
    out = app_module._sync_contacts_html('<div>info</div>', site)
    assert '<script>alert' not in out.split('wb-contacts-map')[0][-200:] \
        or '<script>alert' not in out
    assert '<script>alert(1)</script>' not in out


def test_map_not_injected_into_other_pages(app_context):
    """Инъекция карты — только на страницу «Контакты» (PG-3.2; инцидент
    2026-10-16: карта появлялась на каждой странице, включая созданные
    админом — заказчик видел её на странице 'test', хотя не добавлял)."""
    client = app_module.app.test_client()
    # обычная страница — без карты
    body = client.get('/catalog/').get_data(as_text=True)
    assert 'yandex.ru/map-widget' not in body
    # созданная админом страница — тоже без карты
    admin = app_module.app.test_client()
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    admin.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    with admin.session_transaction() as s:
        s['csrf_token'] = 'csrf-test'
    headers = {'X-CSRF-Token': 'csrf-test', 'Content-Type': 'application/json'}
    r = admin.post('/admin/api/add-page',
                   json={'slug': 'testmap', 'url': '/testmap/', 'title': 'TM'},
                   headers=headers)
    assert r.get_json()['success']
    try:
        assert 'yandex.ru/map-widget' not in client.get('/testmap/').get_data(as_text=True)
    finally:
        admin.post('/admin/api/delete-page', json={'slug': 'testmap'}, headers=headers)
    # страница «Контакты» — карта на месте
    content = app_module.load_content()
    content['pages'].append({
        'slug': 'contacts', 'url': '/contacts/', 'title': 'Контакты',
        'h1': 'Контакты', 'meta_description': '',
        'template': 'pages/contacts.html',
        'content_html': '<div class="cb cb-text"><p>Адрес и телефон</p></div>',
        'blocks': [{'type': 'text', 'text': 'Адрес и телефон'}],
    })
    app_module.save_content(content)
    app_module.invalidate_site_cache()
    contacts = client.get('/contacts/').get_data(as_text=True)
    assert 'yandex.ru/map-widget' in contacts

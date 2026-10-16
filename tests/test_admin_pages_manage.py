# -*- coding: utf-8 -*-
"""Управление страницами в админке + SVG-иконки вместо эмодзи (2026-10-16).

1. Новая страница сразу доступна на сайте по своему URL (добавление «по факту»).
2. Системные страницы (корзина, ЛК и т.п.) нельзя удалить — клиент и сервер.
3. В админке и публичных шаблонах эмодзи заменены на SVG-иконки (sprite).
4. Выбор страницы в списке не уводит с админки; редактирование — отдельной кнопкой.
"""
import re
from pathlib import Path

from werkzeug.security import generate_password_hash

import app as app_module

BASE_DIR = Path(__file__).resolve().parent.parent


def _admin_client():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    client = app_module.app.test_client()
    client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    with client.session_transaction() as s:
        s['csrf_token'] = 'csrf-test'
    client.csrf_headers = {'X-CSRF-Token': 'csrf-test', 'Content-Type': 'application/json'}
    return client


def test_added_page_is_reachable_on_site(app_context):
    """Страница, созданная через «+ Новая страница», реально открывается по URL."""
    client = _admin_client()
    slug = 'testovaya-stranitsa'
    try:
        r = client.post('/admin/api/add-page',
                        json={'slug': slug, 'url': f'/{slug}/', 'title': 'Тестовая страница'},
                        headers=client.csrf_headers)
        assert r.status_code == 200
        assert r.get_json()['success'], r.get_json()

        page = client.get(f'/{slug}/')
        assert page.status_code == 200
        assert 'Тестовая страница' in page.get_data(as_text=True)
    finally:
        client.post('/admin/api/delete-page', json={'slug': slug}, headers=client.csrf_headers)
    # после удаления страница исчезает с сайта
    assert client.get(f'/{slug}/').status_code == 404


def test_cannot_delete_system_page(app_context):
    client = _admin_client()
    for slug in ('basket', 'personal', 'auth', '404'):
        r = client.post('/admin/api/delete-page', json={'slug': slug}, headers=client.csrf_headers)
        assert r.status_code == 400, slug
        assert not r.get_json()['success']
    # корзина по-прежнему на месте
    assert client.get('/basket/').status_code == 200


def test_admin_uses_svg_icons_instead_of_emoji(app_context):
    html = _admin_client().get('/admin/').get_data(as_text=True)
    # sprite встроен и используется
    assert '<symbol id="i-' in html
    assert html.count('<use href="#i-') >= 20
    # эмодзи-пиктограммы убраны из админки
    for emo in ('📊', '🏠', '🎨', '🧱', '📐', '👍', '🛒', '📦', '👥', '💬',
                '📈', '💾', '🛡', '📋', '🔄', '➕', '🗑', '✅', '❌', '🙈', '🔴'):
        assert emo not in html, emo


def test_public_templates_use_svg_icons():
    for name in ('wb_catalog_results.html', 'wb_catalog_block.html',
                 'recommendations_block_inner.html', 'personal.html'):
        text = (BASE_DIR / 'templates' / name).read_text(encoding='utf-8')
        assert '🛒' not in text, name
        assert '❤' not in text, name
        assert '<svg' in text, name


def test_pages_list_select_does_not_redirect(app_context):
    html = _admin_client().get('/admin/').get_data(as_text=True)
    # редактирование — отдельной функцией/кнопкой
    assert 'function editPage(index)' in html
    assert 'editCurrentPage()' in html
    # selectPage только подсвечивает выбранную страницу, без перехода
    m = re.search(r'function selectPage\(index\) \{(.*?)\n        \}', html, re.S)
    assert m, 'selectPage not found'
    assert 'window.location.href' not in m.group(1)
    # системные страницы защищены и на клиенте
    assert 'системная страница' in html


def test_admin_html_js_strings_not_broken(app_context):
    """Регрессия: одинарные кавычки внутри JS-строк с SVG не ломают скрипт
    (инцидент 2026-10-16: class='ic' → SyntaxError, админка целиком не работала)."""
    html = _admin_client().get('/admin/').get_data(as_text=True)
    assert "class='ic'" not in html


def test_page_editor_accepts_trailing_slash(app_context):
    """Редактор страницы открывается и с /admin/page/<slug>, и с trailing slash —
    иначе catch-all роут сайта перехватывал URL и отдавал aspro-404
    (инцидент: созданную страницу невозможно было отредактировать)."""
    client = _admin_client()
    r = client.post('/admin/api/add-page',
                    json={'slug': 'test', 'url': '/test/', 'title': 'Test'},
                    headers=client.csrf_headers)
    assert r.get_json()['success']
    try:
        for u in ('/admin/page/test', '/admin/page/test/'):
            resp = client.get(u)
            assert resp.status_code == 200, (u, resp.status_code)
            assert 'Редактор страницы' in resp.get_data(as_text=True), u
    finally:
        client.post('/admin/api/delete-page', json={'slug': 'test'},
                    headers=client.csrf_headers)

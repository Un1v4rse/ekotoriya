# -*- coding: utf-8 -*-
"""Редактор «на странице» в режиме обёртки: контент страницы грузится в
same-origin iframe (pe_bare=1), media queries применяются честно."""
from werkzeug.security import generate_password_hash

import app as app_module


def csrf():
    return {'X-CSRF-Token': 'csrf', 'Content-Type': 'application/json'}


def _admin_client():
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


def test_admin_edit_page_returns_iframe_wrapper():
    """GET /admin/page/<slug> — обёртка с iframe, без кнопки редактирования."""
    c = _admin_client()
    r = c.get('/admin/page/home')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'pe-content-frame' in html
    assert 'PE_CONTENT_URL' in html
    assert 'page-editor.js' in html
    assert 'page-editor.css' in html
    assert 'data-slug="home"' in html
    # Кнопка «Редактировать страницу» не должна попасть в обёртку
    assert 'admin-edit-fab' not in html


def test_admin_edit_page_wrapper_escapes_url():
    """URL страницы (может содержать кавычки/спецсимволы) экранируется в JS."""
    c = _admin_client()
    content = app_module.load_content()
    page = app_module.get_page_by_slug(content, 'home')
    assert page, 'тестовая страница home должна существовать (conftest)'
    r = c.get('/admin/page/home')
    html = r.get_data(as_text=True)
    assert 'window.PE_CONTENT_URL = "' + (page.get('url') or '/') + '"' in html


def test_pe_bare_page_has_content_without_chrome():
    """GET <url>?pe_bare=1 — контент страницы без кнопки админа и редактора."""
    c = _admin_client()
    r = c.get('/?pe_bare=1')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'admin-edit-fab' not in html      # кнопка подавлена pe_bare
    assert 'page-editor.js' not in html      # редактор не инжектится
    assert 'lazyload-fix.js' in html         # фикс ленивой загрузки остаётся
    assert 'id="content"' in html            # контент страницы на месте
    # Админу с pe_bare отдаём рендер С МАРКЕРАМИ блоков (для редактора в iframe)
    assert '<!--blk:0-->' in html


def test_pe_bare_catalog_page():
    c = _admin_client()
    app_module.wb_sync.sync(token=None, demo=True)   # наполнить витрину демо-товарами
    app_module.invalidate_site_cache()
    r = c.get('/catalog/?pe_bare=1')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'admin-edit-fab' not in html
    assert 'page-editor.js' not in html
    assert 'id="content"' in html
    # Маркеры блоков и реальная витрина каталога (catalog_view) обязаны быть —
    # иначе редактор не сможет смапить блоки страницы.
    assert '<!--blk:0-->' in html
    assert 'Сортировка' in html   # фильтры рендерятся только при переданном catalog_view


def test_admin_edit_page_system_slug_keeps_inline_editor():
    """Системные страницы (404) остаются на старом инлайн-режиме редактора."""
    c = _admin_client()
    _, page = app_module._system_page('404')   # создать запись, если нет
    if not page.get('template'):
        content = app_module.load_content()
        app_module.get_page_by_slug(content, '404')['template'] = 'pages/404.html'
        app_module.save_content(content)
    r = c.get('/admin/page/404')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # старый режим: редактор инжектится прямо в HTML страницы, без обёртки
    assert 'pe-content-frame' not in html
    assert 'data-slug="404"' in html


def test_normal_page_keeps_admin_edit_button():
    """Без pe_bare кнопка «Редактировать страницу» у админа на месте."""
    c = _admin_client()
    r = c.get('/')
    assert r.status_code == 200
    assert 'admin-edit-fab' in r.get_data(as_text=True)

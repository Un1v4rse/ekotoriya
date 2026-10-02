# -*- coding: utf-8 -*-
"""Конструктор сайта: CN-1 (дублирование страниц), CN-2 (html/timer),
CN-3 (пресеты темы), CN-5/CN-7 (черновики, конфликт админов), CN-6 (системные страницы)."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import blocks as blocks_module
import storage
from db import db
from models import AdminUser


@pytest.fixture()
def admin_client():
    """Суперадмин через формальный логин (роль из admin_users)."""
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


def content_client(username, role):
    """Админ-контентщик (не суперадмин) с готовой сессией."""
    db.session.add(AdminUser(username=username,
                             password_hash=generate_password_hash('x' * 10), role=role))
    db.session.commit()
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['admin_user'] = username
        s['admin_role'] = role
        s['csrf_token'] = 'csrf-' + username
    return c


# ---------- CN-1: дублирование страницы ----------

def _add_page(client, slug, title='Тестовая'):
    r = client.post('/admin/api/add-page', json={'slug': slug, 'url': f'/{slug}/', 'title': title},
                    headers=csrf())
    assert r.get_json()['success']
    return r


def test_duplicate_page(admin_client):
    _add_page(admin_client, 'akcii', 'Акции')
    r = admin_client.post('/admin/api/duplicate-page', json={'slug': 'akcii'}, headers=csrf())
    data = r.get_json()
    assert data['success'] and data['slug'] == 'akcii-copy'

    content = app_module.load_content()
    copy = app_module.get_page_by_slug(content, 'akcii-copy')
    assert copy['url'] == '/akcii-copy/'
    assert copy['title'] == 'Акции (копия)'
    assert copy['blocks'] == app_module.get_page_by_slug(content, 'akcii')['blocks']

    # повторное дублирование -> -copy2
    r = admin_client.post('/admin/api/duplicate-page', json={'slug': 'akcii'}, headers=csrf())
    assert r.get_json()['slug'] == 'akcii-copy2'


def test_duplicate_page_not_found(admin_client):
    r = admin_client.post('/admin/api/duplicate-page', json={'slug': 'no-such'}, headers=csrf())
    assert r.status_code == 404


# ---------- CN-2: блоки html и timer ----------

def test_timer_block_render():
    html = blocks_module.render_block({
        'type': 'timer', 'title': 'До конца акции',
        'date': '2030-01-01', 'button_text': 'Купить', 'button_link': '/catalog/'
    })
    assert 'cb-timer' in html
    assert 'data-cb-timer-date="2030-01-01"' in html
    assert 'До конца акции' in html
    assert 'Купить' in html
    # гидратор отсчёта подключается, только если есть таймер
    out = blocks_module.render_blocks([{'type': 'timer', 'date': '2030-01-01'}])
    assert 'cb-timer' in out and 'setInterval' in out
    assert 'TIMER' not in blocks_module.render_blocks([{'type': 'text', 'text': 'x'}])


def test_html_block_restricted_for_anonymous():
    # Без суперадмина (и вообще вне запроса) — заглушка
    assert blocks_module.render_block({'type': 'html', 'html': '<b>raw</b>'}) == \
        '<!-- html block: restricted -->'


def test_empty_html_block_shows_placeholder_for_superadmin():
    # Пустой HTML-блок не должен исчезать с холста — показываем заглушку
    with app_module.app.test_request_context():
        from flask import session
        session['admin_role'] = 'superadmin'
        html = blocks_module.render_block({'type': 'html', 'html': ''})
    assert 'HTML-блок' in html


def test_empty_video_block_shows_placeholder():
    # Пустая/неподдерживаемая ссылка видео — заглушка, а не пустота
    html = blocks_module.render_block({'type': 'video', 'url': ''})
    assert 'Видео' in html
    html2 = blocks_module.render_block({'type': 'video', 'url': 'https://example.com/x'})
    assert 'Видео' in html2
    # валидная ссылка — реальный embed
    html3 = blocks_module.render_block({'type': 'video', 'url': 'https://youtu.be/dQw4w9WgXcQ'})
    assert 'youtube.com/embed' in html3


def test_html_block_renders_for_superadmin():
    with app_module.app.test_request_context():
        from flask import session
        session['admin_role'] = 'superadmin'
        html = blocks_module.render_block({'type': 'html', 'html': '<div class="raw">Свой код</div>'})
    assert '<div class="raw">Свой код</div>' in html


def test_html_block_restricted_for_content_admin():
    c = content_client('editor1', 'content')
    r = c.get('/admin/api/page-blocks?slug=home')
    # контент-админ авторизован, но роль не superadmin — html-блок в рендере страницы заглушкой
    with app_module.app.test_request_context():
        from flask import session
        session['admin_role'] = 'content'
        session['admin_user'] = 'editor1'
        html = blocks_module.render_block({'type': 'html', 'html': '<b>raw</b>'})
    assert html == '<!-- html block: restricted -->'


def test_page_save_strips_html_for_non_superadmin(admin_client):
    c = content_client('editor2', 'content')
    blocks = [{'type': 'heading', 'text': 'Привет'}, {'type': 'html', 'html': '<b>x</b>'}]
    r = c.post('/admin/api/page-save', json={'slug': 'home', 'blocks': blocks},
               headers={'X-CSRF-Token': 'csrf-editor2', 'Content-Type': 'application/json'})
    assert r.get_json()['success']
    saved = admin_client.get('/admin/api/page-blocks?slug=home').get_json()['blocks']
    assert all(b['type'] != 'html' for b in saved)
    assert any(b['type'] == 'heading' for b in saved)
    # суперадмину html-блоки сохраняются
    r = admin_client.post('/admin/api/page-save', json={'slug': 'home', 'blocks': blocks}, headers=csrf())
    assert r.get_json()['success']
    saved = admin_client.get('/admin/api/page-blocks?slug=home').get_json()['blocks']
    assert any(b['type'] == 'html' for b in saved)


# ---------- CN-3: пресеты темы ----------

def test_theme_presets_save_apply_delete_reset(admin_client):
    theme = {'primary_color': '#123456', 'font_size': '18px'}
    r = admin_client.post('/admin/api/theme-presets', json={'action': 'save', 'name': 'Тёмная', 'theme': theme},
                          headers=csrf())
    assert r.get_json()['success']

    r = admin_client.get('/admin/api/theme-presets').get_json()
    assert r['presets']['Тёмная']['primary_color'] == '#123456'

    r = admin_client.post('/admin/api/theme-presets', json={'action': 'apply', 'name': 'Тёмная'}, headers=csrf())
    data = r.get_json()
    assert data['success'] and data['theme']['primary_color'] == '#123456'
    applied = app_module.load_content()['site']['theme']
    assert applied['primary_color'] == '#123456'

    r = admin_client.post('/admin/api/theme-presets', json={'action': 'delete', 'name': 'Тёмная'}, headers=csrf())
    assert r.get_json()['success']
    assert 'Тёмная' not in admin_client.get('/admin/api/theme-presets').get_json()['presets']

    r = admin_client.post('/admin/api/theme-presets', json={'action': 'reset'}, headers=csrf())
    assert r.get_json()['success']
    assert app_module.load_content()['site']['theme']['primary_color'] == \
        app_module.THEME_DEFAULTS['primary_color']


def test_theme_presets_validation(admin_client):
    # невалидные значения заменяются дефолтами, лишние ключи отбрасываются
    theme = {'primary_color': 'red;}',
             'font_size': 'huge',
             'evil': 'body{display:none}'}
    r = admin_client.post('/admin/api/theme-presets', json={'action': 'save', 'name': 'Bad', 'theme': theme},
                          headers=csrf())
    assert r.get_json()['success']
    saved = admin_client.get('/admin/api/theme-presets').get_json()['presets']['Bad']
    assert saved['primary_color'] == app_module.THEME_DEFAULTS['primary_color']
    assert saved['font_size'] == app_module.THEME_DEFAULTS['font_size']
    assert 'evil' not in saved


# ---------- CN-5/CN-7: черновики и конфликт ----------

def test_page_draft_roundtrip(admin_client):
    blocks = [{'type': 'heading', 'text': 'Черновик'}]
    r = admin_client.post('/admin/api/page-draft', json={'slug': 'home', 'blocks': blocks}, headers=csrf())
    assert r.get_json()['success']

    r = admin_client.get('/admin/api/page-draft?slug=home').get_json()
    assert r['success']
    assert r['draft']['blocks'] == blocks
    assert r['draft']['saved_at']


def test_page_draft_requires_admin():
    c = app_module.app.test_client()
    r = c.post('/admin/api/page-draft', json={'slug': 'home', 'blocks': []})
    assert r.status_code in (401, 403)  # 403 от CSRF-гварда раньше авторизации


def test_page_save_conflict_409(admin_client):
    blocks = [{'type': 'heading', 'text': 'Версия 1'}]
    r = admin_client.post('/admin/api/page-save', json={'slug': 'home', 'blocks': blocks}, headers=csrf())
    updated_at = r.get_json()['updated_at']
    assert updated_at

    # обновлённый updated_at отдаётся в page-blocks
    r = admin_client.get('/admin/api/page-blocks?slug=home').get_json()
    assert r['updated_at'] == updated_at

    # чужой/устаревший expected_updated_at -> 409
    r = admin_client.post('/admin/api/page-save',
                          json={'slug': 'home', 'blocks': blocks,
                                'expected_updated_at': '2000-01-01T00:00:00+00:00'},
                          headers=csrf())
    assert r.status_code == 409
    assert 'админом' in r.get_json()['error']

    # без expected_updated_at (подтверждённая перезапись) -> 200
    r = admin_client.post('/admin/api/page-save', json={'slug': 'home', 'blocks': blocks}, headers=csrf())
    assert r.status_code == 200

    # корректный expected_updated_at -> 200
    r = admin_client.post('/admin/api/page-save',
                          json={'slug': 'home', 'blocks': blocks, 'expected_updated_at': updated_at},
                          headers=csrf())
    assert r.status_code == 200


# ---------- CN-6: системные страницы ----------

def test_basket_renders_system_blocks(admin_client):
    # блоки системной страницы 'basket' показываются над содержимым корзины
    c = app_module.app.test_client()
    c.get('/basket/')  # при первом запросе системная страница создаётся
    content = app_module.load_content()
    page = app_module.get_page_by_slug(content, 'basket')
    assert page is not None
    page['blocks'] = [{'type': 'heading', 'text': 'Баннер корзины'}]
    app_module.save_content(content)

    r = c.get('/basket/')
    assert r.status_code == 200
    assert 'Баннер корзины' in r.get_data(as_text=True)


def test_system_pages_created_on_demand():
    c = app_module.app.test_client()
    assert c.get('/basket/').status_code == 200
    assert c.get('/auth/').status_code == 200
    r = c.get('/definitely-not-a-page/')
    assert r.status_code == 404   # создаёт системную страницу '404'
    content = app_module.load_content()
    for slug in ('basket', 'auth', '404'):
        page = app_module.get_page_by_slug(content, slug)
        assert page is not None, f'системная страница {slug} не создана'
        assert isinstance(page['blocks'], list)


def test_404_page_uses_template_and_blocks():
    c = app_module.app.test_client()
    r = c.get('/no-such-page/')
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert 'Страница не найдена' in body
    # системная страница 404 создана и её блоки можно отредактировать
    assert app_module.get_page_by_slug(app_module.load_content(), '404') is not None
    # сам адрес /404/ не должен ронять сайт — это тоже 404-шаблон
    r = c.get('/404/')
    assert r.status_code == 404
    assert 'Страница не найдена' in r.get_data(as_text=True)


def test_system_page_edit_mode_requires_pages_role():
    c = app_module.app.test_client()
    body = c.get('/basket/?edit').get_data(as_text=True)
    assert 'page-editor.js' not in body   # анониму редактор не подключается
    editor = content_client('editor3', 'content')
    r = editor.get('/basket/?edit')
    assert r.status_code == 200
    assert 'page-editor.js' in r.get_data(as_text=True)


def test_empty_gallery_renders_title_in_fallback():
    # Заголовок пустой галереи не должен теряться (рендерился только с картинками)
    html = blocks_module.render_block(
        {'type': 'gallery', 'title': 'Фото производства', 'cols': 3, 'items': [{'src': '', 'alt': ''}]})
    assert 'Фото производства' in html
    assert 'noimage' in html


def test_emptied_features_and_faq_show_placeholder():
    html = blocks_module.render_block({'type': 'features', 'title': 'X', 'items': [{'title': '', 'text': ''}]})
    assert 'Преимущества' in html
    html2 = blocks_module.render_block({'type': 'faq', 'title': 'X', 'items': [{'q': '', 'a': ''}]})
    assert 'FAQ' in html2

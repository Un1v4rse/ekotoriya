# -*- coding: utf-8 -*-
"""Цикл 2 конструктора (сервер): CN-T10 (черновик/публикация/версии),
CN-T9 (мои блоки и шаблоны страниц), CN-T6 (глобальные зоны),
CN-T5 (настройки страницы), CN-T7 (тема: шрифты/история/новые ключи)."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import storage
import zones as zones_module
from db import db
from models import AdminUser, Page, PageVersion

BASE_DIR = app_module.BASE_DIR


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
    """Админ не-суперадмин с готовой сессией."""
    db.session.add(AdminUser(username=username,
                             password_hash=generate_password_hash('x' * 10), role=role))
    db.session.commit()
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['admin_user'] = username
        s['admin_role'] = role
        s['csrf_token'] = 'csrf-' + username
    return c


# ---------- CN-T10.3: черновик → публикация → посетитель видит опубликованное ----------

def test_draft_publish_cycle_visitor_sees_published(admin_client):
    draft_blocks = [{'type': 'heading', 'level': 'h2', 'text': 'Черновик-заголовок'}]
    r = admin_client.post('/admin/api/page-draft', json={'slug': 'home', 'blocks': draft_blocks},
                          headers=csrf())
    assert r.get_json()['success']

    r = admin_client.get('/admin/api/page-draft?slug=home').get_json()
    assert r['success'] and r['has_draft']
    assert r['draft']['blocks'] == draft_blocks
    assert r['draft']['saved_at'] and 'updated_at' in r

    # до публикации посетитель видит старое опубликованное содержимое
    body = app_module.app.test_client().get('/').get_data(as_text=True)
    assert 'Черновик-заголовок' not in body

    # публикация: content_html пересобран из черновика
    r = admin_client.post('/admin/api/page-publish', json={'slug': 'home'}, headers=csrf())
    assert r.get_json()['success']

    body = app_module.app.test_client().get('/').get_data(as_text=True)
    assert 'Черновик-заголовок' in body

    page = db.session.query(Page).filter_by(slug='home').first()
    assert page.published_blocks == draft_blocks
    assert 'Черновик-заголовок' in page.content_html


def test_publish_without_draft_uses_blocks(admin_client):
    r = admin_client.post('/admin/api/page-publish', json={'slug': 'catalog'}, headers=csrf())
    assert r.get_json()['success']
    page = db.session.query(Page).filter_by(slug='catalog').first()
    assert page.published_blocks == (page.blocks or [])


def test_publish_not_found(admin_client):
    r = admin_client.post('/admin/api/page-publish', json={'slug': 'no-such'}, headers=csrf())
    assert r.status_code == 404


def test_page_draft_legacy_settings_fallback(admin_client):
    # старый формат site_settings.page_drafts — фолбэк, если в колонке пусто
    storage.set_setting('page_drafts', {
        'catalog': {'blocks': [{'type': 'heading', 'text': 'Старый черновик'}],
                    'saved_at': '2024-01-01T00:00:00+00:00'}})
    r = admin_client.get('/admin/api/page-draft?slug=catalog').get_json()
    assert r['success']
    assert r['draft']['blocks'][0]['text'] == 'Старый черновик'


# ---------- CN-T10.2: версии страниц ----------

def test_page_versions_save_list_restore(admin_client):
    blocks_v1 = [{'type': 'heading', 'text': 'Версия 1'}]
    admin_client.post('/admin/api/page-draft', json={'slug': 'home', 'blocks': blocks_v1},
                      headers=csrf())
    r = admin_client.post('/admin/api/page-version-save',
                          json={'slug': 'home', 'label': 'Первая'}, headers=csrf())
    assert r.get_json()['success']

    blocks_v2 = [{'type': 'heading', 'text': 'Версия 2'}]
    admin_client.post('/admin/api/page-draft', json={'slug': 'home', 'blocks': blocks_v2},
                      headers=csrf())
    admin_client.post('/admin/api/page-version-save', json={'slug': 'home'}, headers=csrf())

    versions = admin_client.get('/admin/api/page-versions?slug=home').get_json()['versions']
    assert len(versions) == 2
    assert versions[0]['label'] == ''           # новые первые
    assert versions[1]['label'] == 'Первая'
    assert versions[1]['blocks_count'] == 1
    assert 'created_at' in versions[0]

    # откат к первой версии → черновик, БЕЗ автопубликации
    r = admin_client.post('/admin/api/page-version-restore',
                          json={'slug': 'home', 'version_id': versions[1]['id']},
                          headers=csrf())
    assert r.get_json()['success']
    draft = admin_client.get('/admin/api/page-draft?slug=home').get_json()['draft']
    assert draft['blocks'] == blocks_v1
    page = db.session.query(Page).filter_by(slug='home').first()
    assert page.published_blocks != blocks_v1 or page.published_blocks is None \
        or 'Версия 1' not in page.content_html

    # несуществующая версия → 404
    r = admin_client.post('/admin/api/page-version-restore',
                          json={'slug': 'home', 'version_id': 9999}, headers=csrf())
    assert r.status_code == 404


def test_page_versions_limit_10(admin_client):
    for i in range(12):
        admin_client.post('/admin/api/page-version-save', json={'slug': 'home'}, headers=csrf())
    versions = admin_client.get('/admin/api/page-versions?slug=home').get_json()['versions']
    assert len(versions) == 10
    assert db.session.query(PageVersion).filter_by(page_slug='home').count() == 10


def test_publish_creates_auto_version(admin_client):
    admin_client.post('/admin/api/page-publish', json={'slug': 'home'}, headers=csrf())
    versions = admin_client.get('/admin/api/page-versions?slug=home').get_json()['versions']
    assert len(versions) == 1
    assert versions[0]['label'] == 'Авто'


# ---------- CN-T9.1: мои блоки ----------

def test_block_templates_crud(admin_client):
    block = {'type': 'banner', 'title': 'Мой баннер', 'button': 'Купить', 'url': '/catalog/'}
    r = admin_client.post('/admin/api/block-templates',
                          json={'name': 'Баннер акции', 'category': 'Промо', 'block': block},
                          headers=csrf())
    assert r.get_json()['success']
    bid = r.get_json()['id']

    templates = admin_client.get('/admin/api/block-templates').get_json()['templates']
    assert any(t['id'] == bid and t['name'] == 'Баннер акции'
               and t['category'] == 'Промо' and t['block'] == block for t in templates)

    r = admin_client.delete('/admin/api/block-templates', json={'id': bid}, headers=csrf())
    assert r.get_json()['success']
    templates = admin_client.get('/admin/api/block-templates').get_json()['templates']
    assert all(t['id'] != bid for t in templates)

    r = admin_client.delete('/admin/api/block-templates', json={'id': bid}, headers=csrf())
    assert r.status_code == 404


def test_block_templates_validation(admin_client):
    r = admin_client.post('/admin/api/block-templates',
                          json={'block': {'type': 'text'}}, headers=csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/block-templates',
                          json={'name': 'X', 'block': ['not', 'a', 'dict']}, headers=csrf())
    assert r.status_code == 400


# ---------- CN-T9.2: шаблоны страниц ----------

def test_page_templates_list(admin_client):
    templates = admin_client.get('/admin/api/page-templates').get_json()['templates']
    ids = {t['id'] for t in templates}
    assert {'landing', 'showcase', 'contacts'} <= ids
    for t in templates:
        assert t['name'] and t['description'] and isinstance(t['blocks'], list)
        assert len(t['blocks']) > 0


def test_page_templates_create_contacts(admin_client):
    r = admin_client.post('/admin/api/page-templates/create',
                          json={'template_id': 'contacts', 'slug': 'kontakty',
                                'title': 'Контакты'},
                          headers=csrf())
    data = r.get_json()
    assert data['success']
    page = data['page']
    assert page['url'] == '/kontakty/'
    types = [b['type'] for b in page['blocks']]
    assert types == ['heading', 'contacts', 'map', 'form']
    assert page['content_html']

    c = app_module.app.test_client()
    body = c.get('/kontakty/').get_data(as_text=True)
    assert body and '8 800 201 08 24' in body
    assert 'info@ekotoriya.ru' in body
    assert 'map-widget' in body   # адрес карты URL-encoded внутри iframe

    _cleanup_template('kontakty')


def test_page_templates_create_unknown(admin_client):
    r = admin_client.post('/admin/api/page-templates/create',
                          json={'template_id': 'nope', 'slug': 'x1'}, headers=csrf())
    assert r.status_code == 404


def _cleanup_template(slug):
    path = BASE_DIR / 'templates' / 'pages' / f'{slug}.html'
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# ---------- CN-T6: глобальные зоны ----------

def test_global_zones_defaults_not_saved(admin_client):
    r = admin_client.get('/admin/api/global-zones').get_json()
    assert r['success']
    # дефолт создался на лету, в site_settings не сохранился
    assert storage.get_setting('global_zones') is None
    assert r['header']['use_global'] is False
    assert r['header']['phone'] == '8 800 201 08 24'
    assert r['header']['email'] == 'info@ekotoriya.ru'
    assert isinstance(r['header']['menu'], list)
    assert r['footer']['use_global'] is False


def test_global_zones_post_validation_and_persist(admin_client):
    r = admin_client.post('/admin/api/global-zones', json={
        'header': {
            'use_global': True, 'sticky': True, 'height': 90,
            'menu': [{'title': 'Каталог', 'url': '/catalog/',
                      'children': [{'title': 'Плохая ссылка', 'url': 'javascript:alert(1)'}]}],
            'unknown_key': 'должно быть отброшено',
        }}, headers=csrf())
    data = r.get_json()
    assert data['success']
    assert data['header']['use_global'] is True
    assert data['header']['sticky'] is True
    assert data['header']['height'] == 90
    # плохой URL отброшен, неизвестный ключ отброшен
    assert data['header']['menu'][0]['children'][0]['url'] == ''
    assert 'unknown_key' not in data['header']
    # сохранилось
    saved = admin_client.get('/admin/api/global-zones').get_json()
    assert saved['header']['use_global'] is True
    assert storage.get_setting('global_zones')['header']['height'] == 90


def test_render_zone_escapes_xss():
    cfg = zones_module.validate_zone('header', {
        'use_global': True, 'logo_text': '<script>alert(1)</script>',
        'menu': [{'title': '<img src=x onerror=alert(1)>', 'url': '/x/',
                  'children': [{'title': '</a><script>', 'url': '/y/'}]}],
        'phone': '8 800 201 08 24',
    })
    html = zones_module.render_zone('header', cfg, {})
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert '&lt;img' in html
    assert 'zone-menu-children' in html   # двухуровневое меню


def test_global_zones_render_on_new_pages(admin_client):
    admin_client.post('/admin/api/global-zones', json={
        'header': {'use_global': True, 'logo_text': 'ЗонаЛого'},
        'footer': {'use_global': True, 'copyright': 'Подвал2016'},
    }, headers=csrf())
    admin_client.post('/admin/api/add-page',
                      json={'slug': 'zonetest', 'url': '/zonetest/', 'title': 'Зона'},
                      headers=csrf())
    body = app_module.app.test_client().get('/zonetest/').get_data(as_text=True)
    assert 'zone-header' in body and 'ЗонаЛого' in body
    assert 'zone-footer' in body and 'Подвал2016' in body
    _cleanup_template('zonetest')


def test_global_zones_off_by_default_empty_html(admin_client):
    # use_global=False → пустые строки зон на странице из базового шаблона
    admin_client.post('/admin/api/add-page',
                      json={'slug': 'zoneoff', 'url': '/zoneoff/', 'title': 'Без зон'},
                      headers=csrf())
    body = app_module.app.test_client().get('/zoneoff/').get_data(as_text=True)
    assert 'zone-header' not in body
    assert 'page-content' in body
    _cleanup_template('zoneoff')


# ---------- CN-T5: настройки страницы ----------

def test_page_settings_get_defaults_and_post(admin_client):
    r = admin_client.get('/admin/api/page-settings?slug=home').get_json()
    assert r['success']
    assert r['settings']['og_title'] == ''
    assert r['settings']['override_header'] is False

    r = admin_client.post('/admin/api/page-settings', json={
        'slug': 'home',
        'meta': {'title': 'Новый заголовок', 'meta_description': 'Описание'},
        'og_title': 'OG заголовок', 'og_description': 'OG описание',
        'og_image': '/static/uploads/pic.png', 'favicon': '/static/favicon.ico',
        'page_bg': '#f5f5f5', 'page_link_color': '#112233',
    }, headers=csrf())
    assert r.get_json()['success']

    r = admin_client.get('/admin/api/page-settings?slug=home').get_json()
    s = r['settings']
    assert s['og_title'] == 'OG заголовок'
    assert s['og_image'] == '/static/uploads/pic.png'
    assert s['page_bg'] == '#f5f5f5'
    assert r['meta']['title'] == 'Новый заголовок'
    page = db.session.query(Page).filter_by(slug='home').first()
    assert page.title == 'Новый заголовок'


def test_page_settings_bad_og_image_rejected(admin_client):
    r = admin_client.post('/admin/api/page-settings',
                          json={'slug': 'home', 'og_image': 'javascript:alert(1)'},
                          headers=csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/page-settings',
                          json={'slug': 'home', 'favicon': 'data:text/html,x'},
                          headers=csrf())
    assert r.status_code == 400


def test_page_settings_custom_code_superadmin_only(admin_client):
    editor = content_client('editor5', 'content')
    r = editor.post('/admin/api/page-settings',
                    json={'slug': 'home', 'custom_css': 'body{display:none}'},
                    headers={'X-CSRF-Token': 'csrf-editor5',
                             'Content-Type': 'application/json'})
    assert r.status_code == 403
    page = db.session.query(Page).filter_by(slug='home').first()
    assert not (page.settings or {}).get('custom_css')

    r = admin_client.post('/admin/api/page-settings',
                          json={'slug': 'home', 'custom_css': 'body{margin:0}',
                                'custom_js': 'console.log(1)'},
                          headers=csrf())
    assert r.get_json()['success']
    body = app_module.app.test_client().get('/').get_data(as_text=True)
    assert 'body{margin:0}' in body
    assert 'console.log(1)' in body


def test_page_settings_output_on_public_page(admin_client):
    admin_client.post('/admin/api/page-settings', json={
        'slug': 'home', 'og_title': 'OG Хоум', 'og_image': '/static/uploads/og.png',
        'favicon': '/static/favicon.ico', 'page_bg': '#eeeeee',
        'page_link_color': '#123abc',
    }, headers=csrf())
    body = app_module.app.test_client().get('/').get_data(as_text=True)
    assert 'OG Хоум' in body
    assert '/static/uploads/og.png' in body
    assert 'rel="icon" href="/static/favicon.ico"' in body
    assert 'background: #eeeeee' in body
    assert 'color: #123abc' in body


def test_page_settings_not_found(admin_client):
    r = admin_client.get('/admin/api/page-settings?slug=no-such')
    assert r.status_code == 404


# ---------- CN-T7: тема, шрифты, история ----------

def test_theme_endpoint_save_and_history(admin_client):
    theme = {'primary_color': '#223344', 'h1_size': '3rem', 'button_variant': 'outline',
             'heading_font_google': 'Roboto', 'body_font_google': 'Bad Font;}',
             'base_line_height': '1.8'}
    r = admin_client.post('/admin/api/theme', json={'theme': theme}, headers=csrf())
    data = r.get_json()
    assert data['success']
    saved = data['theme']
    assert saved['primary_color'] == '#223344'
    assert saved['h1_size'] == '3rem'
    assert saved['button_variant'] == 'outline'
    assert saved['heading_font_google'] == 'Roboto'
    # шрифт не из вайтлиста отброшен
    assert saved.get('body_font_google', '') == ''
    assert saved['base_line_height'] == '1.8'
    # история: перед сохранением пушнули предыдущее состояние
    assert len(data['history']) == 1

    r = admin_client.get('/admin/api/theme').get_json()
    assert r['theme']['primary_color'] == '#223344'
    assert len(r['history']) == 1

    # откат к записи истории (индекс 0 — состояние до первого сохранения)
    r = admin_client.post('/admin/api/theme',
                          json={'action': 'restore_history', 'index': 0}, headers=csrf())
    data = r.get_json()
    assert data['success']
    assert data['theme']['primary_color'] == app_module.THEME_DEFAULTS['primary_color']
    # перед откатом текущее состояние тоже ушло в историю
    assert len(data['history']) == 2

    r = admin_client.post('/admin/api/theme',
                          json={'action': 'restore_history', 'index': 99}, headers=csrf())
    assert r.status_code == 404


def test_theme_endpoint_validation(admin_client):
    r = admin_client.post('/admin/api/theme', json={'theme': 'not-a-dict'}, headers=csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/theme', json={'theme': {}}, headers=csrf())
    assert r.status_code == 400   # пустая тема — нечего сохранять


def test_theme_css_new_variables_and_import(admin_client):
    admin_client.post('/admin/api/theme', json={'theme': {
        'heading_font_google': 'Playfair Display', 'h1_size': '3.2rem',
        'button_shadow': '2', 'button_variant': 'outline',
    }}, headers=csrf())
    css = app_module.app.test_client().get('/theme.css').get_data(as_text=True)
    assert '--theme-h1: 3.2rem' in css
    assert '--theme-button-shadow: 0 4px 14px' in css
    assert '--theme-button-variant: outline' in css
    assert 'fonts.googleapis.com/css2?family=Playfair+Display' in css
    # существующие переменные на месте
    assert '--theme-primary' in css

    # чужой шрифт (не из вайтлиста) не порождает @import
    admin_client.post('/admin/api/theme', json={
        'theme': {'heading_font_google': 'Evil Font"};body{display:none}'}}, headers=csrf())
    css = app_module.app.test_client().get('/theme.css').get_data(as_text=True)
    assert 'Evil+Font' not in css
    assert 'body{display:none}' not in css


def test_theme_history_limit_10(admin_client):
    for i in range(12):
        admin_client.post('/admin/api/theme',
                          json={'theme': {'primary_color': '#%06x' % (0x100000 + i)}},
                          headers=csrf())
    history = admin_client.get('/admin/api/theme').get_json()['history']
    assert len(history) == 10


def test_fonts_endpoint(admin_client):
    fonts = admin_client.get('/admin/api/fonts').get_json()['fonts']
    assert len(fonts) >= 30
    assert all(f['cyrillic'] and f['category'] in ('sans', 'serif', 'display')
               and f['weights'] for f in fonts)
    families = {f['family'] for f in fonts}
    assert {'Roboto', 'Montserrat', 'PT Sans', 'Lora'} <= families

    fonts = admin_client.get('/admin/api/fonts?q=rob').get_json()['fonts']
    assert any(f['family'] == 'Roboto' for f in fonts)
    assert all('rob' in f['family'].lower() for f in fonts)

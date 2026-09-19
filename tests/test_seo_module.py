# -*- coding: utf-8 -*-
"""SEO-модуль (SEO-1…SEO-8, TZ раздел 4.8): мета страниц, шаблоны карточек,
sitemap, редиректы CSV, JSON-LD (Breadcrumbs/FAQ), аудит."""
import json
import xml.etree.ElementTree as ET

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import storage
import wb_sync


@pytest.fixture()
def admin_client():
    """Авторизованный test_client с CSRF-токеном (по образцу test_admin_api)."""
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


def _csrf(client):
    return {'X-CSRF-Token': 'test-csrf', 'Content-Type': 'application/json'}


def _content():
    return app_module.load_content()


def test_page_save_keywords_canonical(admin_client):
    """SEO-1: page-save сохраняет meta_keywords и canonical."""
    payload = {
        'slug': 'home',
        'page': {
            'title': 'Главная',
            'meta_keywords': 'наматрасники, экотория',
            'canonical': 'https://example.com/',
        },
        'blocks': [{'type': 'heading', 'level': 2, 'text': 'Привет'}],
    }
    r = admin_client.post('/admin/api/page-save', json=payload, headers=_csrf(admin_client))
    assert r.get_json()['success']
    page = app_module.get_page_by_slug(_content(), 'home')
    assert page['meta_keywords'] == 'наматрасники, экотория'
    assert page['canonical'] == 'https://example.com/'
    # page-blocks отдаёт новые поля редактору
    data = admin_client.get('/admin/api/page-blocks?slug=home').get_json()
    assert data['page']['meta_keywords'] == 'наматрасники, экотория'
    assert data['page']['canonical'] == 'https://example.com/'


def test_page_render_canonical_and_keywords():
    """SEO-1: рендер главной содержит canonical и keywords."""
    content = _content()
    page = app_module.get_page_by_slug(content, 'home')
    page['meta_keywords'] = 'наматрасники, экотория'
    page['canonical'] = ''
    storage.save_content(content)
    client = app_module.app.test_client()
    html = client.get('/').get_data(as_text=True)
    assert 'rel="canonical"' in html
    assert '<meta name="keywords" content="наматрасники, экотория"' in html
    # canonical из поля страницы имеет приоритет
    page['canonical'] = 'https://custom.example/page'
    storage.save_content(content)
    html = client.get('/').get_data(as_text=True)
    assert 'href="https://custom.example/page"' in html


def test_render_meta_template_all_variables():
    """SEO-2: подстановка {name} {brand} {price} {category} {discount} {site_name}."""
    product = {
        'name': 'Наматрасник AquaStop',
        'brand': 'Экотория',
        'price': 2000,
        'final_price': 1500,
        'category': 'Наматрасники',
    }
    site = {'name': 'Экотория'}
    title = app_module._render_meta_template(
        '{name} — купить от {price} ₽ | {site_name}', product, site)
    assert title == 'Наматрасник AquaStop — купить от 1500 ₽ | Экотория'
    desc = app_module._render_meta_template(
        '{name}. Бренд {brand}. Цена {price} ₽{discount} ({category})', product, site)
    assert desc == 'Наматрасник AquaStop. Бренд Экотория. Цена 1500 ₽, скидка 25% (Наматрасники)'
    # без скидки {discount} пусто
    product2 = dict(product, price=1500, final_price=1500)
    desc2 = app_module._render_meta_template('{price} ₽{discount}', product2, site)
    assert desc2 == '1500 ₽'
    # битая переменная не роняет рендер
    bad = app_module._render_meta_template('{unknown} {name}', product, site)
    assert bad == '{unknown} {name}'


def test_product_page_uses_meta_templates():
    """SEO-2: карточка товара рендерит title/description по шаблонам."""
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    product = wb_sync.load_products()[0]
    nm, name = product['nm_id'], product['name']
    brand = product.get('brand') or 'Экотория'
    content = _content()
    content['site']['seo']['product_title_template'] = '{name} | {site_name}'
    content['site']['seo']['product_description_template'] = '{brand} за {price} ₽{discount}'
    storage.save_content(content)
    client = app_module.app.test_client()
    html = client.get(f'/catalog/wb/{nm}/').get_data(as_text=True)
    assert f'<title>{name} | Экотория (тест)</title>' in html
    assert f'{brand} за' in html


def test_sitemap_lastmod_priority_and_products():
    """SEO-3: lastmod/priority у страниц, карточки товаров, без системных slug."""
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    content = _content()
    page = app_module.get_page_by_slug(content, 'home')
    page['updated_at'] = '2024-05-01T10:00:00+00:00'
    storage.save_content(content)
    # системная страница существует в контенте — в sitemap попасть не должна
    app_module._system_page('basket')

    client = app_module.app.test_client()
    resp = client.get('/sitemap.xml')
    assert resp.status_code == 200
    root = ET.fromstring(resp.get_data(as_text=True))
    ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    urls = root.findall('sm:url', ns)
    locs = [u.find('sm:loc', ns).text for u in urls]
    assert any(loc.endswith('/catalog/') for loc in locs)
    # товары в sitemap
    assert any(f'/catalog/wb/{p["nm_id"]}/' in loc for loc in locs
               for p in wb_sync.load_products())
    # приоритеты: главная 1.0, каталог 0.8
    by_loc = {u.find('sm:loc', ns).text: u for u in urls}
    home = next(u for u in urls if u.find('sm:loc', ns).text.endswith('://localhost/'))
    assert home.find('sm:priority', ns).text == '1.0'
    catalog = next(u for u in urls if u.find('sm:loc', ns).text.endswith('/catalog/'))
    assert catalog.find('sm:priority', ns).text == '0.8'
    # lastmod у главной из updated_at
    assert home.find('sm:lastmod', ns).text == '2024-05-01'
    # системные slug'и не включены
    assert not any('/basket/' in loc for loc in locs)
    assert not any('/personal/' in loc for loc in locs)
    assert not any('/auth/' in loc for loc in locs)


def test_redirects_import(admin_client):
    """SEO-3: импорт CSV — добавление, skip дубликатов, валидация мусора."""
    text = '/old-a/,/catalog/\n/old-b/;/contacts/\n/old-c/ → /help/\n' \
           'битая-строка\nno-slash,/catalog/\n/old-a/,/help/'
    r = admin_client.post('/admin/api/redirects-import', json={'text': text},
                          headers=_csrf(admin_client))
    result = r.get_json()
    assert result['success']
    assert result['added'] == 3
    assert result['skipped'] == 3   # битая строка, путь без '/', дубликат
    redirects = _content()['site']['seo']['redirects']
    assert redirects['/old-a/'] == '/catalog/'
    assert redirects['/old-b/'] == '/contacts/'
    assert redirects['/old-c/'] == '/help/'
    # редирект реально работает
    client = app_module.app.test_client()
    assert client.get('/old-a/').status_code == 301


def test_seo_audit_finds_issues(admin_client):
    """SEO-8: аудит находит пустые description, дубли title, битые ссылки."""
    content = _content()
    pages = content['pages']
    pages[0]['meta_description'] = ''          # no_description
    pages[0]['h1'] = ''                        # no_h1
    pages[1]['title'] = 'Одинаковый'           # duplicate_title
    pages[2]['title'] = 'Одинаковый'
    pages[0]['blocks'] = [{
        'type': 'banner', 'title': 'Акция',
        'url': '/no-such-page/',                # broken_link
        'image': '/static/uploads/pic.jpg',     # ok — /static/
    }]
    storage.save_content(content)
    data = admin_client.get('/admin/api/seo-audit').get_json()
    assert data['success']
    types = {i['type'] for i in data['issues']}
    assert 'no_description' in types
    assert 'no_h1' in types
    assert 'duplicate_title' in types
    assert 'broken_link' in types
    broken = [i for i in data['issues'] if i['type'] == 'broken_link']
    assert any('/no-such-page/' in i['detail'] for i in broken)


def test_seo_audit_requires_auth():
    client = app_module.app.test_client()
    assert client.get('/admin/api/seo-audit').status_code == 401
    # POST без CSRF отсекается csrf_guard ещё до проверки авторизации
    assert client.post('/admin/api/redirects-import', json={'text': '/a/,/b/'}).status_code in (401, 403)


def test_faq_jsonld_on_page_with_faq_block():
    """SEO-4: JSON-LD FAQPage при наличии faq-блока."""
    content = _content()
    page = app_module.get_page_by_slug(content, 'home')
    page['blocks'] = [{
        'type': 'faq', 'title': 'Вопросы',
        'items': [{'q': 'Какая доставка?', 'a': 'По всей России.'}],
    }]
    storage.save_content(content)
    client = app_module.app.test_client()
    html = client.get('/').get_data(as_text=True)
    assert '"@type":"FAQPage"' in html
    assert 'Какая доставка?' in html
    # JSON-LD валиден
    start = html.index('"@type":"FAQPage"')
    script_start = html.rindex('<script type="application/ld+json">', 0, start)
    script_end = html.index('</script>', start)
    json.loads(html[script_start + len('<script type="application/ld+json">'):script_end])


def test_breadcrumbs_jsonld_present():
    """SEO-4: JSON-LD BreadcrumbList на страницах."""
    client = app_module.app.test_client()
    html = client.get('/catalog/namatrasniki/').get_data(as_text=True)
    assert '"@type":"BreadcrumbList"' in html
    assert 'Главная' in html
    assert 'Наматрасники' in html


def test_product_page_breadcrumbs_and_og_type():
    """SEO-4/SEO-5: крошки карточки и og:type=product."""
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    nm = wb_sync.load_products()[0]['nm_id']
    client = app_module.app.test_client()
    html = client.get(f'/catalog/wb/{nm}/').get_data(as_text=True)
    assert '"@type":"BreadcrumbList"' in html
    assert 'og:type" content="product"' in html
    assert 'twitter:card' in html
    assert 'rel="canonical"' in html


def test_counters_and_og_tags_rendered():
    """SEO-5/SEO-7: OG/Twitter-теги и счётчики из site.seo в head."""
    content = _content()
    content['site']['seo']['yandex_metrika_id'] = '12345678'
    content['site']['seo']['google_analytics_id'] = 'G-TEST123'
    storage.save_content(content)
    client = app_module.app.test_client()
    html = client.get('/').get_data(as_text=True)
    assert 'og:title' in html
    assert 'og:url' in html
    assert 'twitter:title' in html
    assert 'twitter:image' in html
    assert 'ym(12345678, "init"' in html
    assert 'googletagmanager.com/gtag/js?id=G-TEST123' in html

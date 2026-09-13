#!/usr/bin/env python3
"""
Экотория — многостраничный клон сайта с простой админкой.

Запуск:
    python app.py

Админка:
    http://127.0.0.1:5000/admin
    логин: admin
    пароль: admin
"""
import hashlib
import json
import mimetypes
import os
import re
import shutil
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, render_template, render_template_string, request, redirect, url_for, jsonify, Response, send_from_directory

# Static assets with unusual extensions (Bitrix legacy)
mimetypes.add_type('text/javascript', '.php')
mimetypes.add_type('text/css', '.less')

import customers
import ozon_sync
import wb_sync
import blocks as blocks_module
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'content.json'
IMAGE_CACHE_DIR = BASE_DIR / 'data' / 'image_cache'
IMAGE_CACHE_DIR.mkdir(exist_ok=True)
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin')
ADMIN_PASS_HASH = generate_password_hash(ADMIN_PASS)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ekotoriya-secret-key')
app.json.ensure_ascii = False


@app.after_request
def add_cache_headers(response):
    """Add long-term cache for static assets and short/no cache for HTML pages."""
    path = request.path
    if path.startswith('/static/') or path == '/theme.css' or path.startswith('/proxy/image'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif path.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico', '.woff', '.woff2')):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    else:
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
    return response


def load_content():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_content(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def check_auth(username, password):
    return username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                'Требуется авторизация',
                401,
                {'WWW-Authenticate': 'Basic realm="Admin Panel"'}
            )
        return f(*args, **kwargs)
    return decorated


def get_page_by_slug(content, slug):
    """Find page by internal slug (e.g. catalog__namatrasniki)."""
    for page in content['pages']:
        if page['slug'] == slug:
            return page
    return None


def get_page_by_url(content, url):
    """Find page by public URL (e.g. /catalog/namatrasniki/)."""
    for page in content['pages']:
        if page['url'].rstrip('/') == url.rstrip('/'):
            return page
    return None


THEME_DEFAULTS = {
    'primary_color': '#b49d84',
    'secondary_color': '#9a8269',
    'accent_color': '#137333',
    'text_color': '#333333',
    'bg_color': '#ffffff',
    'font_family': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    'font_size': '16px',
    'version': 1,
}

SEO_DEFAULTS = {
    'yandex_metrika_id': '96433627',
    'google_analytics_id': '',
    'robots_txt': 'User-agent: *\nDisallow: /admin\nAllow: /\n',
    'extra_head': '',
}


def ensure_site_defaults(content):
    if 'site' not in content:
        content['site'] = {}
    if 'theme' not in content['site']:
        content['site']['theme'] = dict(THEME_DEFAULTS)
    else:
        for k, v in THEME_DEFAULTS.items():
            content['site']['theme'].setdefault(k, v)
    if 'seo' not in content['site']:
        content['site']['seo'] = dict(SEO_DEFAULTS)
    else:
        for k, v in SEO_DEFAULTS.items():
            content['site']['seo'].setdefault(k, v)
    return content


def load_content():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return ensure_site_defaults(json.load(f))


def make_context(content, page):
    wb_products = wb_sync.load_products()
    for p in wb_products:
        p['final_price'] = wb_sync.final_price(p)
    ozon_products = ozon_sync.load_products()
    for p in ozon_products:
        p['final_price'] = ozon_sync.final_price(p)
    all_products = wb_products + ozon_products
    popular = customers.get_popular_products(all_products, limit=8)
    ctx = {
        'site': content.get('site', {}),
        'page': page,
        'menu': content.get('menu', []),
        'socials': content.get('socials', []),
        'wb_products': wb_products,
        'wb_demo': wb_sync.load_json(wb_sync.PRODUCTS_FILE).get('demo', False),
        'ozon_products': ozon_products,
        'ozon_demo': ozon_sync.load_json(ozon_sync.PRODUCTS_FILE).get('demo', False),
        'recommended_products': popular,
    }
    return ctx


@app.route('/')
def index():
    content = load_content()
    page = get_page_by_slug(content, 'index')
    if not page:
        return 'Index page not found', 404
    return render_template(page['template'], **make_context(content, page))


# Fallback routes for legacy /bitrix/... and /upload/... URLs that some
# scripts request with a trailing slash or without /static prefix.
@app.route('/bitrix/<path:path>')
def bitrix_static_redirect(path):
    return redirect('/static/bitrix/' + path, code=301)


@app.route('/bitrix/<path:path>/')
def bitrix_static_trailing(path):
    return redirect('/static/bitrix/' + path, code=301)


@app.route('/upload/<path:path>')
def upload_static_redirect(path):
    return redirect('/static/upload/' + path, code=301)


@app.route('/upload/<path:path>/')
def upload_static_trailing(path):
    return redirect('/static/upload/' + path, code=301)


@app.route('/ajax/bottom_panel.php', methods=['GET', 'POST'])
@app.route('/ajax/bottom_panel.php/', methods=['GET', 'POST'])
def ajax_bottom_panel():
    return Response('', status=200)


@app.route('/ajax/getAjaxBasket.php', methods=['GET', 'POST'])
@app.route('/ajax/getAjaxBasket.php/', methods=['GET', 'POST'])
def ajax_basket():
    return jsonify({'success': True, 'basket': []}), 200


@app.route('/static/bitrix/tools/conversion/ajax_counter.php', methods=['GET', 'POST'])
def ajax_counter():
    return Response('', status=200)


@app.route('/favicon.ico')
@app.route('/favicon.ico/')
def favicon():
    return send_from_directory(BASE_DIR / 'static', 'favicon.ico')


@app.route('/basket/')
def basket_stub():
    """Basket placeholder — real checkout is handled by one-click orders."""
    return render_template_string('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Корзина</title><link rel="stylesheet" href="/theme.css"></head><body style="padding:2rem;font-family:sans-serif;"><h1>Корзина</h1><p>Оформление заказа происходит через кнопку «Купить в 1 клик» на карточке товара или по телефону {{ site.phone }}.</p><p><a href="/">Вернуться на главную</a></p></body></html>', site=load_content().get('site', {}))


@app.route('/personal/')
@app.route('/personal/favorite/')
@app.route('/personal/orders/')
def personal_stub():
    """Personal account placeholder."""
    return render_template_string('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Личный кабинет</title><link rel="stylesheet" href="/theme.css"></head><body style="padding:2rem;font-family:sans-serif;"><h1>Личный кабинет</h1><p>Личный кабинет в разработке. Для проверки заказов свяжитесь с нами по телефону {{ site.phone }}.</p><p><a href="/">Вернуться на главную</a></p></body></html>', site=load_content().get('site', {}))


@app.route('/theme.css')
def theme_css():
    """Generate CSS from site theme settings."""
    content = load_content()
    theme = content.get('site', {}).get('theme', {})
    css = f""":root {{
    --theme-primary: {theme.get('primary_color', '#b49d84')};
    --theme-secondary: {theme.get('secondary_color', '#9a8269')};
    --theme-accent: {theme.get('accent_color', '#137333')};
    --theme-text: {theme.get('text_color', '#333333')};
    --theme-bg: {theme.get('bg_color', '#ffffff')};
}}
body {{
    font-family: {theme.get('font_family', '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif')};
    font-size: {theme.get('font_size', '16px')};
    color: var(--theme-text);
    background-color: var(--theme-bg);
}}
header, .header, .top-block-wrapper, .top_blocks, .front .top_blocks, .footer {{
    font-family: {theme.get('font_family', '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif')};
}}
"""
    return Response(css, mimetype='text/css')


@app.route('/robots.txt')
def robots_txt():
    content = load_content()
    seo = content.get('site', {}).get('seo', {})
    return Response(seo.get('robots_txt', 'User-agent: *\nDisallow: /admin\nAllow: /\n'), mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    content = load_content()
    site_url = request.host_url.rstrip('/')
    xml = ['<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']
    for page in content.get('pages', []):
        xml.append(f'<url><loc>{site_url}{page["url"]}</loc><priority>0.8</priority></url>\n')
    xml.append('</urlset>')
    return Response(''.join(xml), mimetype='application/xml')


@app.route('/static/<path:path>/')
def static_trailing_slash(path):
    """Some scripts request static files with a trailing slash; redirect to the real file."""
    return redirect('/static/' + path, code=301)


@app.route('/<path:path>/')
def page_route(path):
    """Render any page by its URL path (e.g. /catalog/namatrasniki/)."""
    content = load_content()
    # Normalize path
    path = path.strip('/')
    # Try direct URL match first
    url = '/' + path + '/'
    page = get_page_by_url(content, url)
    if page:
        return render_template(page['template'], **make_context(content, page))
    # Try slug match (double underscores represent slashes)
    slug = path.replace('/', '__').replace('-', '_')
    page = get_page_by_slug(content, slug)
    if page:
        return render_template(page['template'], **make_context(content, page))
    # Fallback: if a parent path exists (e.g. product detail link under /catalog/namatrasniki/),
    # redirect to the nearest parent page to avoid 404s from original product cards.
    parts = path.split('/')
    for i in range(len(parts) - 1, 0, -1):
        parent_url = '/' + '/'.join(parts[:i]) + '/'
        parent_page = get_page_by_url(content, parent_url)
        if parent_page:
            return redirect(parent_url, code=302)
    return 'Страница не найдена', 404


@app.route('/admin')
@admin_required
def admin():
    content = load_content()
    # Отдаём админке облегчённые данные: тяжёлый content_html (сотни КБ на страницу)
    # подгружается по запросу только если нужно (блок «дизайн исходной страницы»).
    slim = dict(content)
    slim['pages'] = []
    for p in content.get('pages', []):
        sp = dict(p)
        sp['content_html'] = ''
        slim['pages'].append(sp)
    return render_template('admin.html', content=slim)


@app.route('/admin/api/page-html')
@admin_required
def admin_page_html():
    """Полный content_html страницы по slug (для редактирования перенесённого дизайна)."""
    slug = request.args.get('slug', '').strip()
    content = load_content()
    page = get_page_by_slug(content, slug)
    if not page:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    return jsonify({'success': True, 'content_html': page.get('content_html', '')})


@app.route('/admin/api/full-content')
@admin_required
def admin_full_content():
    """Полный content.json (включая content_html всех страниц) — для бэкапа."""
    return jsonify(load_content())


@app.route('/admin/page/<slug>')
@admin_required
def admin_edit_page(slug):
    """Режим редактирования прямо на странице сайта (как в Tilda).

    Рендерит страницу в её реальном шаблоне, но content_html собирается
    из блоков с маркерами, а в страницу встраивается редактор-оверлей.
    """
    content = load_content()
    page = get_page_by_slug(content, slug)
    if not page:
        return 'Страница не найдена', 404
    blocks = page.get('blocks')
    if not isinstance(blocks, list):
        blocks = [{'type': 'html', 'legacy': True, 'html': page.get('content_html', '')}]
    edit_page = dict(page)
    edit_page['content_html'] = blocks_module.render_blocks_marked(blocks)
    html = render_template(page['template'], **make_context(content, edit_page))
    inject = (
        '<link rel="stylesheet" href="/static/css/page-editor.css">\n'
        f'<script src="/static/js/page-editor.js" data-slug="{slug}" defer></script>\n'
    )
    if '</body>' in html:
        html = html.replace('</body>', inject + '</body>', 1)
    else:
        html += inject
    return Response(html)


@app.route('/admin/api/page-blocks')
@admin_required
def admin_page_blocks():
    """Блоки страницы по slug (для редактора на странице)."""
    slug = request.args.get('slug', '').strip()
    content = load_content()
    page = get_page_by_slug(content, slug)
    if not page:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    blocks = page.get('blocks')
    if not isinstance(blocks, list):
        blocks = [{'type': 'html', 'legacy': True, 'html': page.get('content_html', '')}]
    return jsonify({
        'success': True,
        'page': {k: page.get(k, '') for k in ('slug', 'url', 'title', 'h1', 'meta_description')},
        'blocks': blocks,
        'legacy': any(isinstance(b, dict) and b.get('legacy') for b in blocks),
    })


@app.route('/admin/api/preview-marked', methods=['POST'])
@admin_required
def admin_preview_marked():
    """Отрендерить блоки с маркерами (для мгновенного обновления страницы в редакторе)."""
    payload = request.get_json() or {}
    blocks = payload.get('blocks') or []
    return jsonify({'success': True, 'content_html': blocks_module.render_blocks_marked(blocks)})


@app.route('/admin/api/page-save', methods=['POST'])
@admin_required
def admin_page_save():
    """Сохранить одну страницу (мета + блоки) из редактора на странице."""
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    content = load_content()
    page = get_page_by_slug(content, slug)
    if not page:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    blocks = payload.get('blocks')
    if not isinstance(blocks, list):
        return jsonify({'success': False, 'error': 'blocks обязателен'}), 400
    meta = payload.get('page') or {}
    for k in ('title', 'h1', 'meta_description'):
        if k in meta:
            page[k] = meta[k]
    page['blocks'] = blocks
    page['content_html'] = blocks_module.render_blocks(blocks)
    save_content(content)
    return jsonify({'success': True})


@app.route('/admin/api/preview', methods=['POST'])
@admin_required
def admin_preview():
    """Живой предпросмотр: рендерит страницу из переданных блоков без сохранения.

    Используется конструктором страниц (WYSIWYG-редактирование как в Tilda):
    админка шлёт текущее состояние блоков, сервер возвращает готовый HTML
    страницы в её реальном шаблоне.
    """
    payload = request.get_json() or {}
    slug = payload.get('slug', '')
    page_fields = payload.get('page') or {}
    blocks = payload.get('blocks') or []
    content = load_content()
    page = get_page_by_slug(content, slug)
    if page is None:
        # Страница ещё не сохранена — показываем в базовом шаблоне сайта
        page = {
            'slug': slug or 'preview',
            'url': page_fields.get('url', '/'),
            'template': BASE_PAGE_TEMPLATE,
            'title': '',
            'h1': '',
            'meta_description': '',
        }
    preview_page = dict(page)
    for k in ('title', 'h1', 'meta_description', 'url'):
        if page_fields.get(k):
            preview_page[k] = page_fields[k]
    preview_page['content_html'] = blocks_module.render_blocks(blocks)
    try:
        html = render_template(preview_page['template'], **make_context(content, preview_page))
        return jsonify({'success': True, 'html': html})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/admin/api/save', methods=['POST'])
@admin_required
def admin_save():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No JSON received'}), 400
    # Basic validation: must contain site, pages, menu keys
    if 'site' not in data or 'pages' not in data or 'menu' not in data:
        return jsonify({'success': False, 'error': 'Invalid structure'}), 400
    # Страницы, собранные в визуальном конструкторе (blocks), рендерим в content_html.
    # Страницы без blocks не трогаем: админка работает с облегчённой копией данных
    # (без content_html), поэтому берём исходный HTML из сохранённого файла.
    stored = load_content()
    stored_pages = {p['slug']: p for p in stored.get('pages', [])}
    for page in data.get('pages', []):
        if isinstance(page.get('blocks'), list):
            page['content_html'] = blocks_module.render_blocks(page['blocks'])
        elif page.get('slug') in stored_pages:
            page['content_html'] = stored_pages[page['slug']].get('content_html', '')
    save_content(data)
    return jsonify({'success': True})


@app.route('/admin/api/upload', methods=['POST'])
@admin_required
def admin_upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename'}), 400
    upload_dir = BASE_DIR / 'static' / 'uploads'
    upload_dir.mkdir(exist_ok=True)
    filename = file.filename
    path = upload_dir / filename
    file.save(path)
    return jsonify({'success': True, 'url': f'/static/uploads/{filename}'})


BASE_PAGE_TEMPLATE = 'pages/_new_page_base.html'


@app.route('/admin/api/add-page', methods=['POST'])
@admin_required
def admin_add_page():
    """Add a new page to the site. Creates a copy of the base template."""
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    title = payload.get('title', '').strip()
    url = payload.get('url', '').strip()
    if not slug or not url:
        return jsonify({'success': False, 'error': 'slug и url обязательны'}), 400
    if not re.match(r'^[a-z0-9_\-]+$', slug):
        return jsonify({'success': False, 'error': 'slug может содержать только латинские буквы, цифры, - и _'}), 400
    content = load_content()
    if any(p['slug'] == slug for p in content['pages']):
        return jsonify({'success': False, 'error': 'Страница с таким slug уже существует'}), 400
    if any(p['url'] == url for p in content['pages']):
        return jsonify({'success': False, 'error': 'Страница с таким URL уже существует'}), 400

    template_name = f'pages/{slug}.html'
    dest = BASE_DIR / 'templates' / template_name
    base = BASE_DIR / 'templates' / BASE_PAGE_TEMPLATE
    if base.exists():
        shutil.copy(base, dest)
    else:
        # Minimal fallback template
        dest.write_text("""<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"UTF-8\">\n<title>{{ page.title }}</title>\n<meta name=\"description\" content=\"{{ page.meta_description }}\">\n<link rel=\"stylesheet\" href=\"/theme.css?v={{ site.theme.version | default(1) }}\">\n</head>\n<body>\n{{ page.content_html | safe }}\n</body>\n</html>\n""", encoding='utf-8')

    content['pages'].append({
        'slug': slug,
        'url': url,
        'title': title or slug,
        'h1': title or slug,
        'meta_description': '',
        'template': template_name,
        'blocks': [
            {'type': 'heading', 'level': 'h1', 'text': title or slug},
            {'type': 'text', 'text': 'Опишите здесь, чем полезна эта страница. Текст можно форматировать абзацами.'},
        ],
    })
    content['pages'][-1]['content_html'] = blocks_module.render_blocks(content['pages'][-1]['blocks'])
    save_content(content)
    return jsonify({'success': True, 'page': content['pages'][-1]})


@app.route('/admin/api/delete-page', methods=['POST'])
@admin_required
def admin_delete_page():
    """Delete a page by slug and optionally remove its template."""
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    if not slug:
        return jsonify({'success': False, 'error': 'slug обязателен'}), 400
    content = load_content()
    page = next((p for p in content['pages'] if p['slug'] == slug), None)
    if not page:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    content['pages'] = [p for p in content['pages'] if p['slug'] != slug]
    save_content(content)
    template_path = BASE_DIR / 'templates' / page.get('template', '')
    try:
        if template_path.exists() and template_path != BASE_DIR / 'templates' / BASE_PAGE_TEMPLATE:
            template_path.unlink()
    except Exception as e:
        return jsonify({'success': True, 'warning': str(e)})
    return jsonify({'success': True})


@app.route('/admin/api/rebuild', methods=['POST'])
@admin_required
def admin_rebuild():
    """Re-run build_template.py to refresh templates from static_original."""
    import subprocess
    try:
        subprocess.run(['python', str(BASE_DIR / 'build_template.py')], check=True, cwd=str(BASE_DIR))
        return jsonify({'success': True})
    except subprocess.CalledProcessError as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/api/wb-products')
@admin_required
def admin_wb_products():
    """Return current Wildberries products with final prices."""
    products = wb_sync.load_products()
    for p in products:
        p['final_price'] = wb_sync.final_price(p)
    return jsonify({'success': True, 'products': products, 'demo': wb_sync.load_json(wb_sync.PRODUCTS_FILE).get('demo', False)})


@app.route('/admin/api/wb-sync', methods=['POST'])
@admin_required
def admin_wb_sync():
    """Sync products from Wildberries Seller API."""
    token = wb_sync.get_wb_token()
    result = wb_sync.sync(token=token, demo=not token)
    return jsonify(result)


@app.route('/admin/api/wb-discount', methods=['POST'])
@admin_required
def admin_wb_discount():
    """Set additional discount percent for a WB product."""
    data = request.get_json() or {}
    product_id = data.get('product_id') or data.get('nm_id')
    percent = data.get('percent', 0)
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'percent must be integer'}), 400
    if not product_id:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    wb_sync.set_admin_discount(int(product_id), max(0, min(99, percent)))
    return jsonify({'success': True})


@app.route('/api/wb-products')
def api_wb_products():
    """Public endpoint for WB products (used by catalog pages)."""
    products = wb_sync.load_products()
    for p in products:
        p['final_price'] = wb_sync.final_price(p)
    return jsonify({'success': True, 'products': products})


@app.route('/catalog/wb/<int:nm_id>/')
def wb_product_page(nm_id):
    """Dedicated page for a WB product."""
    product = next((p for p in wb_sync.load_products() if p['nm_id'] == nm_id), None)
    if not product:
        return 'Товар не найден', 404
    product['final_price'] = wb_sync.final_price(product)
    content = load_content()
    return render_template('pages/catalog__namatrasniki.html',
                           **make_context(content, get_page_by_slug(content, 'catalog__namatrasniki')),
                           wb_product=product)


@app.route('/api/register', methods=['POST'])
def api_register():
    """Register a new customer (or update existing by phone)."""
    data = request.get_json() or request.form.to_dict() or {}
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Имя и телефон обязательны'}), 400
    customer = customers.add_customer(name, phone, email)
    return jsonify({'success': True, 'customer': customer})


@app.route('/api/order', methods=['POST'])
def api_order():
    """Create a new order from public form (one-click buy)."""
    data = request.get_json() or request.form.to_dict() or {}
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    product_id = data.get('product_id') or data.get('nm_id')
    product_name = data.get('product_name', '').strip()
    price = data.get('price', 0)
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Имя и телефон обязательны'}), 400
    try:
        if product_id and isinstance(product_id, str) and product_id.isdigit():
            product_id = int(product_id)
    except (TypeError, ValueError):
        product_id = 0
    try:
        price = int(price)
    except (TypeError, ValueError):
        price = 0
    order = customers.add_order(name, phone, email, product_id, product_name, price)
    return jsonify({'success': True, 'order': order})


@app.route('/api/like', methods=['POST'])
def api_like():
    """Register a public like for a product."""
    data = request.get_json() or request.form.to_dict() or {}
    product_id = data.get('product_id') or data.get('nm_id')
    product_name = data.get('product_name', '').strip()
    try:
        if product_id and isinstance(product_id, str) and product_id.isdigit():
            product_id = int(product_id)
    except (TypeError, ValueError):
        product_id = 0
    if not product_id:
        return jsonify({'success': False, 'error': 'product_id обязателен'}), 400
    customers.add_like(product_id, product_name)
    return jsonify({'success': True, 'likes': customers.get_like_counts().get(product_id, 0)})


@app.route('/admin/api/customers')
@admin_required
def admin_api_customers():
    """Return all customers for admin panel."""
    return jsonify({'success': True, 'customers': customers.get_customers()})


@app.route('/admin/api/orders')
@admin_required
def admin_api_orders():
    """Return all orders for admin panel."""
    return jsonify({'success': True, 'orders': customers.get_orders()})


@app.route('/admin/api/order-status', methods=['POST'])
@admin_required
def admin_api_order_status():
    """Update order status."""
    data = request.get_json() or {}
    order_id = data.get('id')
    status = data.get('status', '').strip()
    if not order_id or not status:
        return jsonify({'success': False, 'error': 'id и status обязательны'}), 400
    order = customers.update_order_status(int(order_id), status)
    if not order:
        return jsonify({'success': False, 'error': 'Заявка не найдена'}), 404
    return jsonify({'success': True, 'order': order})


@app.route('/admin/api/ozon-products')
@admin_required
def admin_ozon_products():
    """Return current Ozon products with final prices."""
    products = ozon_sync.load_products()
    for p in products:
        p['final_price'] = ozon_sync.final_price(p)
    return jsonify({'success': True, 'products': products, 'demo': ozon_sync.load_json(ozon_sync.PRODUCTS_FILE).get('demo', False)})


@app.route('/admin/api/ozon-sync', methods=['POST'])
@admin_required
def admin_ozon_sync():
    """Sync products from Ozon Seller API."""
    client_id, api_key = ozon_sync.get_ozon_credentials()
    result = ozon_sync.sync(client_id=client_id, api_key=api_key, demo=not client_id or not api_key)
    return jsonify(result)


@app.route('/admin/api/ozon-discount', methods=['POST'])
@admin_required
def admin_ozon_discount():
    """Set additional discount percent for an Ozon product."""
    data = request.get_json() or {}
    product_id = data.get('product_id') or data.get('offer_id')
    percent = data.get('percent', 0)
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'percent must be integer'}), 400
    if not product_id:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    ozon_sync.set_admin_discount(product_id, max(0, min(99, percent)))
    return jsonify({'success': True})


@app.route('/api/ozon-products')
def api_ozon_products():
    """Public endpoint for Ozon products."""
    products = ozon_sync.load_products()
    for p in products:
        p['final_price'] = ozon_sync.final_price(p)
    return jsonify({'success': True, 'products': products})


def ensure_placeholder():
    """Create a local placeholder image for products without available photos."""
    placeholder = BASE_DIR / 'static' / 'noimage.png'
    if placeholder.exists():
        return placeholder
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (600, 400), (240, 240, 240))
        draw = ImageDraw.Draw(img)
        text = 'Нет фото'
        try:
            font = ImageFont.truetype('arial.ttf', 40)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((600 - tw) / 2, (400 - th) / 2), text, fill=(120, 120, 120), font=font)
        img.save(str(placeholder), 'PNG')
    except Exception:
        placeholder.write_bytes(b'')
    return placeholder


@app.route('/proxy/image')
def proxy_image():
    """Proxy and cache external product images to avoid ORB/CORS issues.

    If the upstream image is unreachable, a local placeholder is returned so
    the layout does not break.
    """
    url = request.args.get('url', '').strip()
    if not url or not (url.startswith('http://') or url.startswith('https://')):
        return send_from_directory(BASE_DIR / 'static', 'noimage.png')
    # Safety: only allow known market image hosts
    allowed_hosts = ('basket-', 'cdn1.ozone.ru', 'cdn', 'ozone.ru', 'wildberries.ru', 'wb.ru')
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    if not any(h in host for h in allowed_hosts):
        return send_from_directory(BASE_DIR / 'static', 'noimage.png')
    cache_key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:32]
    ext = Path(urlparse(url).path).suffix or '.jpg'
    if ext.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.bmp', '.ico'):
        ext = '.jpg'
    cache_file = IMAGE_CACHE_DIR / f"{cache_key}{ext}"
    if not cache_file.exists():
        try:
            r = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            })
            if r.status_code == 200:
                cache_file.write_bytes(r.content)
        except Exception:
            pass
    if cache_file.exists():
        content_type, _ = mimetypes.guess_type(str(cache_file))
        if not content_type:
            content_type = 'image/jpeg'
        return send_from_directory(
            IMAGE_CACHE_DIR,
            cache_file.name,
            mimetype=content_type,
            max_age=86400 * 30,
        )
    placeholder = ensure_placeholder()
    return send_from_directory(
        BASE_DIR / 'static',
        'noimage.png',
        mimetype='image/png',
        max_age=86400 * 30,
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

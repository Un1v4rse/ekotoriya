#!/usr/bin/env python3
"""
Экотория — многостраничный клон сайта с простой админкой.

Запуск:
    python app.py

Админка:
    http://127.0.0.1:5000/admin
    Логин/пароль: env ADMIN_USER/ADMIN_PASS, иначе — файл data/admin_credentials.json.
    При первом запуске с дефолтным паролем генерируется случайный пароль,
    который выводится в консоль один раз и требует смены при входе.
    Сессионный ключ: env SECRET_KEY (без него сессии сбрасываются при перезапуске).
"""
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, Response, send_from_directory

# Static assets with unusual extensions (Bitrix legacy)
mimetypes.add_type('text/javascript', '.php')
mimetypes.add_type('text/css', '.less')

import customers
import wb_sync
import blocks as blocks_module
import storage
import db as db_module
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'content.json'
IMAGE_CACHE_DIR = BASE_DIR / 'data' / 'image_cache'
IMAGE_CACHE_DIR.mkdir(exist_ok=True)
ADMIN_CREDENTIALS_FILE = BASE_DIR / 'data' / 'admin_credentials.json'

app = Flask(__name__)
# SECRET_KEY только из окружения: без него сессии не переживают перезапуск.
if os.environ.get('SECRET_KEY'):
    app.secret_key = os.environ['SECRET_KEY']
else:
    app.secret_key = secrets.token_hex(32)
    print('[admin] SECRET_KEY не задан в окружении: сгенерирован случайный ключ, '
          'сессии будут сбрасываться при перезапуске сервера.')
app.json.ensure_ascii = False

# База данных (AR-1): SQLite по умолчанию, DATABASE_URL для PostgreSQL.
db_module.init_app(app)

# Кэширование (AR-3): SimpleCache по умолчанию, тип — через env CACHE_TYPE.
from flask_caching import Cache
cache = Cache(app, config={
    'CACHE_TYPE': os.environ.get('CACHE_TYPE', 'SimpleCache'),
    'CACHE_DEFAULT_TIMEOUT': 300,
})


def invalidate_site_cache():
    """Инвалидация кэша сайта после изменений в админке / синка (AR-3)."""
    try:
        cache.clear()
    except Exception:
        pass
    storage.invalidate_cache()


@cache.memoize(timeout=300)
def _products_cached(include_hidden=False):
    """Единый каталог WB+Ozon (CT-1) с итоговыми ценами (кэш AR-3).

    По умолчанию — только visible-товары (WB-5/OZ-4) и с учётом настройки
    «скрывать нулевые остатки» (CT-4). include_hidden=True — для админки.
    """
    import pricing
    from db import db as _db
    from models import Product
    rows = _db.session.query(Product).order_by(Product.id).all()
    products = [p.to_product_dict() for p in rows]
    if not include_hidden:
        site = load_content().get('site', {}) or {}
        hide_zero = bool(site.get('hide_zero_stock'))
        products = [p for p in products
                    if p.get('visible') and not (hide_zero and not p.get('stock'))]
    return pricing.enrich_products(products)


def get_products():
    return _products_cached()


@cache.memoize(timeout=300)
def _popular_cached():
    """Рекомендации «популярное» (RS-2/RS-5): движок recommendations.py —
    веса заказ/лайк/просмотр + затухание по времени (RS-3/RS-4)."""
    import recommendations
    return recommendations.popular_products(_products_cached(), limit=8)


def _session_id():
    """Анонимный id сессии для событий и персональных рекомендаций."""
    sid = session.get('sid')
    if not sid:
        sid = secrets.token_hex(8)
        session['sid'] = sid
    return sid


def _rec_block_items(products):
    """Адаптер для recommendations_block.html: [{product, orders, likes, score}]."""
    from collections import Counter
    from db import db
    from models import Order
    order_counts = Counter(str(o.product_id or '') for o in
                           db.session.query(Order).all())
    like_counts = customers.get_like_counts()
    items = []
    for p in products:
        pid = str(p.get('nm_id') or p.get('id') or '')
        like_key = int(pid) if pid.isdigit() else pid
        items.append({
            'product': p,
            'orders': order_counts.get(pid, 0),
            'likes': like_counts.get(like_key, 0),
            'score': p.get('rec_score', 0),
        })
    return items


def load_admin_credentials():
    """Читает логин/хэш пароля из data/admin_credentials.json (если файл есть)."""
    if ADMIN_CREDENTIALS_FILE.exists():
        try:
            return json.loads(ADMIN_CREDENTIALS_FILE.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    return None


def save_admin_credentials(user, password_hash, must_change_password=False):
    """Сохраняет учётные данные в файл и возвращает их dict."""
    data = {'user': user, 'hash': password_hash, 'must_change_password': must_change_password}
    ADMIN_CREDENTIALS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def init_admin_credentials():
    """Определяет учётные данные админа.

    Приоритет: env ADMIN_USER/ADMIN_PASS (перезаписывают файл). Дальше —
    сохранённый файл. Если пароль дефолтный (admin/admin из env или из
    умолчаний), генерируем случайный, показываем ОДИН раз в консоли и
    требуем смены пароля при первом входе.
    """
    env_user = os.environ.get('ADMIN_USER')
    env_pass = os.environ.get('ADMIN_PASS')
    if env_user and env_pass and env_pass != 'admin':
        return save_admin_credentials(env_user, generate_password_hash(env_pass))
    saved = load_admin_credentials()
    if saved and saved.get('user') and saved.get('hash'):
        return saved
    user = 'admin'
    password = secrets.token_urlsafe(9)
    creds = save_admin_credentials(user, generate_password_hash(password), must_change_password=True)
    print('=' * 64)
    print('[admin] Пароль по умолчанию небезопасен — сгенерирован случайный пароль:')
    print(f'[admin]   логин:    {user}')
    print(f'[admin]   пароль:   {password}')
    print('[admin] Пароль показывается один раз; при первом входе его нужно будет сменить.')
    print('=' * 64)
    return creds


ADMIN_CREDENTIALS = init_admin_credentials()


@app.after_request
def add_cache_headers(response):
    """Add long-term cache for static assets and short/no cache for HTML pages.

    Error responses must never be cached long-term: a cached 404 (e.g. for a
    lazy-loaded include that did not exist yet) would otherwise stick in the
    browser for a year and break the site's lazy-load queue permanently.
    """
    path = request.path
    if response.status_code >= 400:
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
    elif path in ('/static/js/page-editor.js', '/static/css/page-editor.css', '/static/js/wb-actions.js'):
        # Активно разрабатываемые ассеты редактора — кэшировать нельзя,
        # иначе браузер держит старую версию после обновлений.
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
    elif path.startswith('/static/include/'):
        # Lazy-loaded HTML fragments (Bitrix-style .php includes). Must be
        # text/html: as text/javascript jQuery would eval the whole HTML
        # response as a script and the site's lazy-load queue would stall.
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
    elif path.startswith('/static/') or path == '/theme.css' or path.startswith('/proxy/image'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif path.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico', '.woff', '.woff2')):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    else:
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
    return response


def check_auth(username, password):
    return username == ADMIN_CREDENTIALS.get('user') and check_password_hash(ADMIN_CREDENTIALS['hash'], password)


def get_csrf_token():
    """CSRF-токен сессии (генерируется один раз за сессию)."""
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_hex(16)
        session['csrf_token'] = token
    return token


@app.before_request
def csrf_guard():
    """Проверка CSRF-токена для изменяющих запросов под /admin/*.

    Токен берём из заголовка X-CSRF-Token или поля csrf_token формы
    и сравниваем со значением в сессии. Форма входа не требует токена.
    """
    if not request.path.startswith('/admin/'):
        return None
    if request.path == '/admin/login':
        return None
    if request.method not in ('POST', 'PUT', 'DELETE'):
        return None
    token = request.headers.get('X-CSRF-Token', '') or request.form.get('csrf_token', '')
    if not token or token != session.get('csrf_token'):
        if request.path.startswith('/admin/api/'):
            return jsonify({'success': False, 'error': 'Неверный или отсутствующий CSRF-токен'}), 403
        return 'Неверный или отсутствующий CSRF-токен', 403
    return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_user'):
            if request.path.startswith('/admin/api/'):
                return jsonify({'success': False, 'error': 'Требуется авторизация'}), 401
            return redirect(url_for('admin_login', next=request.path))
        # Пароль не сменён после первого входа — никуда, кроме формы смены, нельзя
        if (ADMIN_CREDENTIALS.get('must_change_password')
                and request.path not in ('/admin/change-password', '/admin/logout')):
            if request.path.startswith('/admin/api/'):
                return jsonify({'success': False, 'error': 'Требуется смена пароля'}), 403
            return redirect(url_for('admin_change_password'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Форма входа в админку: сессионная авторизация вместо Basic Auth."""
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if check_auth(username, password):
            session['admin_user'] = username
            session.pop('csrf_token', None)  # новый токен для новой сессии
            if ADMIN_CREDENTIALS.get('must_change_password'):
                return redirect(url_for('admin_change_password'))
            return redirect(request.args.get('next') or url_for('admin'))
        error = 'Неверный логин или пароль'
    return render_template('admin_login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    """Выход из админки: полная очистка сессии."""
    session.clear()
    return redirect(url_for('admin_login'))


@app.route('/admin/change-password', methods=['GET', 'POST'])
@admin_required
def admin_change_password():
    """Смена пароля админки (принудительная после первого входа)."""
    error = None
    if request.method == 'POST':
        old = request.form.get('old_password', '')
        new = request.form.get('new_password', '')
        repeat = request.form.get('repeat_password', '')
        user = session['admin_user']
        if not check_auth(user, old):
            error = 'Текущий пароль указан неверно'
        elif len(new) < 8:
            error = 'Новый пароль должен быть не короче 8 символов'
        elif new != repeat:
            error = 'Новые пароли не совпадают'
        else:
            creds = save_admin_credentials(user, generate_password_hash(new), must_change_password=False)
            ADMIN_CREDENTIALS.update(creds)
            session.pop('csrf_token', None)
            return redirect(url_for('admin'))
    return render_template('admin_change_password.html', error=error, csrf_token=get_csrf_token())


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
    'border_radius': '6px',
    'card_bg': '#ffffff',
    'button_text_color': '#ffffff',
    'link_color': '#9a8269',
    'heading_font_family': '',
    'container_width': '1400px',
    'version': 1,
}

SEO_DEFAULTS = {
    'yandex_metrika_id': '96433627',
    'google_analytics_id': '',
    'robots_txt': 'User-agent: *\nDisallow: /admin\nAllow: /\n',
    'extra_head': '',
    'product_title_template': '{name} — купить в интернет-магазине Экотория',
    'product_description_template': '{name}. Бренд {brand}. Цена {price} ₽. Доставка по России.',
    'redirects': {},
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
    """Контент сайта из БД (AR-1). Формат dict — как у старого content.json.

    Пока таблица pages пуста (миграция не прогнана), storage читает
    data/content.json как фолбэк — сайт работает на любом этапе перехода.
    """
    return ensure_site_defaults(storage.load_content())


def save_content(data):
    """Сохранить контент в БД (транзакционно, upsert по slug)."""
    storage.save_content(data)


def _absolute_url(url, host_url):
    """Сделать URL абсолютным (для Schema.org/OG нужны полные адреса)."""
    url = str(url or '').strip()
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    return host_url.rstrip('/') + (url if url.startswith('/') else '/' + url)


def _seo_org(content, host_url):
    """Organization (JSON-LD): название, логотип, контакты — на всех страницах."""
    site = content.get('site', {})
    org = {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        'name': site.get('title', ''),
        'url': host_url,
    }
    logo = _absolute_url(site.get('logo'), host_url)
    if logo:
        org['logo'] = logo
    if site.get('phone'):
        org['telephone'] = site['phone']
    if site.get('email'):
        org['email'] = site['email']
    return org


def make_context(content, page):
    all_products = get_products()
    popular = _popular_cached()
    host_url = request.host_url
    site = content.get('site', {})
    ctx = {
        'site': site,
        'page': page,
        'menu': content.get('menu', []),
        'socials': content.get('socials', []),
        'wb_products': all_products,
        'wb_demo': wb_sync.load_json(wb_sync.PRODUCTS_FILE).get('demo', False),
        'recommended_products': _rec_block_items(popular),
        'seo_org': _seo_org(content, host_url),
        'seo_og_image': _absolute_url(site.get('logo'), host_url) or (host_url.rstrip('/') + '/static/noimage.png'),
        'seo_product': None,
    }
    # Главная: персональные рекомендации по истории сессии/клиента (RS-2, 3.3).
    # Аноним с пустой историей получит fallback на популярное (RS-5).
    if page.get('slug') == 'index':
        try:
            import recommendations
            personal = recommendations.personal_products(
                all_products, customer_id=session.get('customer_id'),
                session_id=_session_id())
            ctx['personal_products'] = _rec_block_items(personal)
        except Exception:
            ctx['personal_products'] = []
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


def _css_color(value, default):
    """Безопасный HEX-цвет для theme.css (защита от подстановки CSS-кода)."""
    value = str(value or '').strip()
    return value if re.fullmatch(r'#[0-9a-fA-F]{3,8}', value) else default


def _css_size(value, default):
    """Безопасный CSS-размер: число + px/rem/em/%/vw/vh (без скобок и точек с запятой)."""
    value = str(value or '').strip()
    return value if re.fullmatch(r'\d+(\.\d+)?(px|rem|em|%|vw|vh)', value) else default


@app.route('/theme.css')
def theme_css():
    """Generate CSS from site theme settings."""
    content = load_content()
    theme = content.get('site', {}).get('theme', {})
    font_family = theme.get('font_family') or THEME_DEFAULTS['font_family']
    if not re.fullmatch(r'[^{}<>;]+', font_family):
        font_family = THEME_DEFAULTS['font_family']
    heading_font = theme.get('heading_font_family') or font_family
    css = f""":root {{
    --theme-primary: {_css_color(theme.get('primary_color'), '#b49d84')};
    --theme-secondary: {_css_color(theme.get('secondary_color'), '#9a8269')};
    --theme-accent: {_css_color(theme.get('accent_color'), '#137333')};
    --theme-text: {_css_color(theme.get('text_color'), '#333333')};
    --theme-bg: {_css_color(theme.get('bg_color'), '#ffffff')};
    --theme-border-radius: {_css_size(theme.get('border_radius'), '6px')};
    --theme-card-bg: {_css_color(theme.get('card_bg'), '#ffffff')};
    --theme-button-text: {_css_color(theme.get('button_text_color'), '#ffffff')};
    --theme-link: {_css_color(theme.get('link_color'), '#9a8269')};
    --theme-heading-font: {heading_font};
    --theme-container-width: {_css_size(theme.get('container_width'), '1400px')};
}}
body {{
    font-family: {font_family};
    font-size: {_css_size(theme.get('font_size'), '16px')};
    color: var(--theme-text);
    background-color: var(--theme-bg);
}}
header, .header, .top-block-wrapper, .top_blocks, .front .top_blocks, .footer {{
    font-family: {font_family};
}}
h1, h2, h3, .h1, .h2, .h3 {{
    font-family: var(--theme-heading-font);
}}
a {{
    color: var(--theme-link);
}}
.maxwidth-theme {{
    max-width: var(--theme-container-width);
}}
/* Скругления и фоны по теме (важнее инлайн-стилей блоков) */
.cb-banner-btn, .cb-form-fields button, .cb-review-card, .cb-faq details,
.wb-product-card, .prod-card {{
    border-radius: var(--theme-border-radius) !important;
}}
.wb-product-card {{
    background: var(--theme-card-bg) !important;
}}
.cb-form-fields button {{
    color: var(--theme-button-text);
}}
"""
    return Response(css + BLOCKS_CSS, mimetype='text/css')


# Стили блоков конструктора страниц (cb-*): подключаются через /theme.css на всех страницах
BLOCKS_CSS = """
/* Отзывы */
.cb-reviews { padding: 2rem 0; }
.cb-reviews h2, .cb-form h2, .cb-map h2, .cb-faq h2 { font-size: 1.6rem; margin-bottom: 1.5rem; }
.cb-reviews-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
.cb-review-card { border: 1px solid #e0e0e0; border-radius: 10px; padding: 1rem; background: #fff; }
.cb-review-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; }
.cb-review-name { font-weight: 600; }
.cb-review-stars { color: #e8a33d; letter-spacing: 1px; }
.cb-review-card p { margin: 0; font-size: 0.92rem; line-height: 1.5; }
/* Форма обратной связи */
.cb-form { padding: 2rem 0; }
.cb-form-fields { display: flex; flex-wrap: wrap; gap: 0.6rem; max-width: 660px; }
.cb-form-fields input[type="text"], .cb-form-fields input[type="tel"], .cb-form-fields input[type="email"] {
    flex: 1 1 180px; padding: 0.55rem 0.7rem; border: 1px solid #ddd; border-radius: 6px; font-size: 0.95rem;
}
.cb-form-fields button { background: #b49d84; color: #fff; border: 0; padding: 0.55rem 1.6rem; border-radius: 6px; font-weight: 600; cursor: pointer; }
.cb-form-fields button:hover { background: #9a8269; }
/* Карта */
.cb-map { padding: 2rem 0; }
.cb-map iframe { width: 100%; height: 420px; border: 0; border-radius: 10px; display: block; }
/* FAQ (аккордеон) */
.cb-faq { padding: 2rem 0; }
.cb-faq details { border: 1px solid #e0e0e0; border-radius: 8px; background: #fff; margin-bottom: 0.6rem; padding: 0.4rem 1rem; }
.cb-faq summary { font-weight: 600; cursor: pointer; padding: 0.5rem 0; }
.cb-faq details p { margin: 0 0 0.8rem; line-height: 1.5; }
/* Баннер/акция */
.cb-banner { margin: 1.5rem 0; }
.cb-banner-inner { border-radius: 12px; padding: 2.5rem 2rem; text-align: center; color: #fff; }
.cb-banner-title { font-size: 1.6rem; font-weight: 700; margin: 0 0 0.5rem; }
.cb-banner-sub { margin: 0 0 1.2rem; opacity: 0.92; }
.cb-banner-btn { display: inline-block; background: #fff; color: #333; padding: 0.7rem 1.8rem; border-radius: 6px; text-decoration: none; font-weight: 600; }
.cb-banner-btn:hover { opacity: 0.9; }
/* Текст + картинка */
.cb-text-image { padding: 2rem 0; }
.cb-section { }
.cb-ti-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; align-items: center; }
.cb-ti-grid h2 { font-size: 1.6rem; margin: 0 0 1rem; }
.cb-ti-grid p { line-height: 1.6; margin: 0 0 0.8rem; }
@media (max-width: 767px) { .cb-ti-grid { grid-template-columns: 1fr; } }
/* Галерея */
.cb-gallery { padding: 2rem 0; }
.cb-gallery h2 { font-size: 1.6rem; margin-bottom: 1.5rem; }
.cb-gallery-grid { display: grid; gap: 1rem; }
.cb-gallery-c2 .cb-gallery-grid { grid-template-columns: repeat(2, 1fr); }
.cb-gallery-c3 .cb-gallery-grid { grid-template-columns: repeat(3, 1fr); }
.cb-gallery-c4 .cb-gallery-grid { grid-template-columns: repeat(4, 1fr); }
.cb-gallery-item img { width: 100%; height: 220px; object-fit: cover; border-radius: 10px; display: block; }
@media (max-width: 767px) { .cb-gallery-c3 .cb-gallery-grid, .cb-gallery-c4 .cb-gallery-grid { grid-template-columns: repeat(2, 1fr); } }
/* Преимущества (тизеры) */
.cb-features { padding: 2rem 0; }
.cb-features h2 { font-size: 1.6rem; margin-bottom: 1.5rem; }
.cb-features-grid { display: grid; gap: 1rem; }
.cb-features-c2 .cb-features-grid { grid-template-columns: repeat(2, 1fr); }
.cb-features-c3 .cb-features-grid { grid-template-columns: repeat(3, 1fr); }
.cb-features-c4 .cb-features-grid { grid-template-columns: repeat(4, 1fr); }
.cb-features-c5 .cb-features-grid { grid-template-columns: repeat(5, 1fr); }
.cb-feature-card { text-align: center; padding: 1.2rem 0.8rem; }
.cb-feature-icon { height: 42px; width: auto; margin-bottom: 0.6rem; }
span.cb-feature-icon { display: block; height: auto; width: 100%; }
.cb-feature-title { font-weight: 600; margin-bottom: 0.3rem; }
.cb-feature-text { font-size: 0.88rem; color: #666; line-height: 1.45; }
@media (max-width: 767px) { .cb-features-c4 .cb-features-grid, .cb-features-c5 .cb-features-grid { grid-template-columns: repeat(2, 1fr); } }
/* Таблица */
.cb-table { padding: 2rem 0; }
.cb-table h2 { font-size: 1.6rem; margin-bottom: 1.5rem; }
.cb-table-wrap { overflow-x: auto; }
.cb-table table { width: 100%; border-collapse: collapse; background: #fff; }
.cb-table td { border: 1px solid #e0e0e0; padding: 0.6rem 0.9rem; font-size: 0.95rem; }
.cb-table th { border: 1px solid #e0e0e0; padding: 0.6rem 0.9rem; font-size: 0.95rem; background: #b49d84; color: #fff; text-align: left; font-weight: 600; }
/* Текст в колонках */
.cb-text-columns { padding: 2rem 0; }
.cb-text-columns h2 { font-size: 1.6rem; margin-bottom: 1.5rem; }
.cb-cols-grid { display: grid; gap: 2rem; }
.cb-cols-2 .cb-cols-grid { grid-template-columns: repeat(2, 1fr); }
.cb-cols-3 .cb-cols-grid { grid-template-columns: repeat(3, 1fr); }
.cb-cols-4 .cb-cols-grid { grid-template-columns: repeat(4, 1fr); }
.cb-col p { line-height: 1.6; margin: 0 0 0.8rem; }
@media (max-width: 767px) { .cb-cols-3 .cb-cols-grid, .cb-cols-4 .cb-cols-grid { grid-template-columns: 1fr; } }
/* Контакты */
.cb-contacts { padding: 2rem 0; }
.cb-contacts h2 { font-size: 1.6rem; margin-bottom: 1.5rem; }
.cb-contacts-card { border: 1px solid #e0e0e0; border-radius: 10px; background: #fff; padding: 1.2rem 1.4rem; max-width: 640px; }
.cb-contact-row { display: flex; gap: 1rem; padding: 0.5rem 0; border-bottom: 1px solid #f0f0f0; }
.cb-contact-row:last-child { border-bottom: 0; }
.cb-contact-row > span:first-child { width: 130px; color: #888; flex-shrink: 0; }
.cb-contact-row a { color: #b49d84; }
"""


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
    # 301-редиректы из настроек SEO: точное совпадение пути {"/old/": "/new/"}
    redirects = content.get('site', {}).get('seo', {}).get('redirects') or {}
    if isinstance(redirects, dict):
        target = redirects.get('/' + path + '/') or redirects.get('/' + path)
        if target:
            return redirect(target, code=301)
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
@app.route('/admin/')
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
    return render_template('admin.html', content=slim, csrf_token=get_csrf_token())


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
    editor_ver = int(os.path.getmtime(os.path.join(BASE_DIR, 'static', 'js', 'page-editor.js')))
    inject = (
        f'<link rel="stylesheet" href="/static/css/page-editor.css?v={editor_ver}">\n'
        f'<script>window.CSRF_TOKEN = "{get_csrf_token()}";</script>\n'
        f'<script src="/static/js/page-editor.js?v={editor_ver}" data-slug="{slug}" defer></script>\n'
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
    invalidate_site_cache()
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
    invalidate_site_cache()
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
    invalidate_site_cache()
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
    invalidate_site_cache()
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
    """Каталог для админки: ВСЕ товары всех источников, включая скрытые."""
    return jsonify({
        'success': True,
        'products': _products_cached(include_hidden=True),
        'demo': wb_sync.load_json(wb_sync.PRODUCTS_FILE).get('demo', False),
    })


@app.route('/admin/api/wb-sync', methods=['POST'])
@admin_required
def admin_wb_sync():
    """Sync products from Wildberries Seller API."""
    token = wb_sync.get_wb_token()
    result = wb_sync.sync(token=token, demo=not token)
    invalidate_site_cache()
    return jsonify(result)


@app.route('/admin/api/wb-sync-light', methods=['POST'])
@admin_required
def admin_wb_sync_light():
    """Лёгкая синхронизация WB: только цены и остатки."""
    result = wb_sync.sync_light(token=wb_sync.get_wb_token())
    invalidate_site_cache()
    return jsonify(result)


@app.route('/admin/api/wb-discount', methods=['POST'])
@admin_required
def admin_wb_discount():
    """Совместимость: индивидуальная скидка на товар WB (PR-3).

    Делегирует общей логике /admin/api/product-discount. Push в WB
    не выполняется — скидки живут только на сайте (PR-5).
    """
    data = request.get_json() or {}
    data.setdefault('source', 'wb')
    return _product_discount_response(data)


@app.route('/admin/api/product-discount', methods=['POST'])
@admin_required
def admin_product_discount():
    """Индивидуальная скидка на товар любого источника (PR-3, PR-4, PR-7)."""
    return _product_discount_response(request.get_json() or {})


def _product_discount_response(data):
    source = data.get('source', 'wb')
    product_id = data.get('product_id') or data.get('nm_id')
    discount_type = data.get('discount_type', 'percent')
    value = data.get('value', data.get('percent', 0))
    if discount_type not in ('percent', 'amount'):
        return jsonify({'success': False, 'error': 'discount_type must be percent|amount'}), 400
    try:
        value = int(value)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'value must be integer'}), 400
    if not product_id:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    ok = _set_product_discount(source, product_id, discount_type, value)
    if not ok:
        return jsonify({'success': False, 'error': 'Товар не найден'}), 404
    _audit_log('discount.set', 'product', f'{source}:{product_id}', {
        'discount_type': discount_type, 'value': value,
    })
    invalidate_site_cache()
    return jsonify({'success': True})


def _set_product_discount(source, product_id, discount_type, value):
    """Проставить скидку админа товару в БД (PR-3, PR-4)."""
    from db import db
    from models import Product
    prod = db.session.query(Product).filter_by(
        source=source, nm_id=int(product_id)).first()
    if prod is None:
        return False
    if discount_type == 'amount':
        prod.admin_discount_type = 'amount'
        prod.admin_discount_amount = max(0, int(value))
        prod.admin_discount_percent = 0
    else:
        prod.admin_discount_type = 'percent'
        prod.admin_discount_percent = max(0, min(99, int(value)))
        prod.admin_discount_amount = 0
    db.session.commit()
    return True


@app.route('/admin/api/wb-discount-bulk', methods=['POST'])
@admin_required
def admin_wb_discount_bulk():
    """Совместимость: массовая скидка выбранным товарам WB (PR-3)."""
    data = request.get_json() or {}
    nm_ids = data.get('nm_ids') or []
    data['scope'] = 'selected'
    data['items'] = [{'source': 'wb', 'product_id': nm} for nm in nm_ids]
    return _discount_bulk_response(data)


@app.route('/admin/api/discount-bulk', methods=['POST'])
@admin_required
def admin_discount_bulk():
    """Массовая скидка (PR-3): selected / category / source / all."""
    return _discount_bulk_response(request.get_json() or {})


def _discount_bulk_response(data):
    scope = data.get('scope', 'selected')
    discount_type = data.get('discount_type', 'percent')
    value = data.get('value', data.get('percent', 0))
    if discount_type not in ('percent', 'amount'):
        return jsonify({'success': False, 'error': 'discount_type must be percent|amount'}), 400
    try:
        value = int(value)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'value must be integer'}), 400

    if scope == 'selected':
        items = data.get('items') or []
        if not items:
            return jsonify({'success': False, 'error': 'items required'}), 400
        count = 0
        for it in items:
            if _set_product_discount(it.get('source', 'wb'), it.get('product_id'),
                                     discount_type, value):
                count += 1
        _audit_log('discount.bulk', 'products', f'{scope}:{count}', {
            'discount_type': discount_type, 'value': value,
            'items': [f"{it.get('source','wb')}:{it.get('product_id')}" for it in items],
        })
        invalidate_site_cache()
        return jsonify({'success': True, 'count': count})

    # scope = category | source | all — правило в таблице discounts
    scope_value = {
        'category': data.get('category', ''),
        'source': data.get('source', ''),
        'all': '',
    }.get(scope)
    if scope_value is None:
        return jsonify({'success': False, 'error': 'scope must be selected|category|source|all'}), 400
    if scope in ('category', 'source') and not scope_value:
        return jsonify({'success': False, 'error': f'{scope} required'}), 400

    from db import db
    from models import Discount
    (db.session.query(Discount)
     .filter_by(scope=scope, scope_value=scope_value, active=True)
     .update({'active': False}))
    rule = Discount(scope=scope, scope_value=scope_value,
                    discount_type=discount_type, value=max(0, value), active=True,
                    created_by=session.get('admin_user') or 'admin')
    db.session.add(rule)
    db.session.commit()
    _audit_log('discount.rule', scope, scope_value, {
        'discount_type': discount_type, 'value': value, 'rule_id': rule.id,
    })
    invalidate_site_cache()
    return jsonify({'success': True, 'rule_id': rule.id})


@app.route('/admin/api/discount-rules')
@admin_required
def admin_discount_rules():
    """Активные правила скидок category/source/all (PR-3)."""
    from db import db
    from models import Discount
    rules = (db.session.query(Discount)
             .filter_by(active=True)
             .order_by(Discount.id.desc()).all())
    return jsonify({'success': True, 'rules': [{
        'id': r.id, 'scope': r.scope, 'scope_value': r.scope_value,
        'discount_type': r.discount_type, 'value': r.value,
        'created_by': r.created_by or '',
        'created_at': r.created_at.isoformat(timespec='seconds') if r.created_at else '',
    } for r in rules]})


@app.route('/admin/api/discount-rule/delete', methods=['POST'])
@admin_required
def admin_discount_rule_delete():
    data = request.get_json() or {}
    rule_id = data.get('id')
    if not rule_id:
        return jsonify({'success': False, 'error': 'id required'}), 400
    from db import db
    from models import Discount
    rule = db.session.get(Discount, int(rule_id))
    if rule is None:
        return jsonify({'success': False, 'error': 'Правило не найдено'}), 404
    rule.active = False
    db.session.commit()
    _audit_log('discount.rule_delete', rule.scope, rule.scope_value, {'rule_id': rule.id})
    invalidate_site_cache()
    return jsonify({'success': True})


@app.route('/admin/api/export-settings', methods=['GET', 'POST'])
@admin_required
def admin_export_settings():
    """Режим выгрузки товаров на сайт (WB-5, OZ-4): all/selected/category."""
    import export_filter
    if request.method == 'GET':
        source = request.args.get('source', 'wb')
        return jsonify({'success': True, 'settings': export_filter.get_export_settings(source)})

    data = request.get_json() or {}
    source = data.get('source', 'wb')
    if source not in ('wb', 'ozon'):
        return jsonify({'success': False, 'error': 'source must be wb|ozon'}), 400
    mode = data.get('mode', 'all')
    if mode not in ('all', 'selected', 'category'):
        return jsonify({'success': False, 'error': 'mode must be all|selected|category'}), 400
    selected_ids = [int(x) for x in (data.get('selected_ids') or []) if str(x).isdigit()]
    category = str(data.get('category') or '')

    content = load_content()
    site = content.setdefault('site', {})
    site[f'{source}_export'] = {
        'mode': mode, 'selected_ids': selected_ids, 'category': category,
    }
    save_content(content)
    affected = export_filter.reapply_visibility(source)
    _audit_log('export.settings', source, mode, {
        'selected_ids': selected_ids, 'category': category, 'affected': affected,
    })
    invalidate_site_cache()
    return jsonify({'success': True, 'affected': affected})


@app.route('/admin/api/ozon-sync', methods=['POST'])
@admin_required
def admin_ozon_sync():
    """Полная синхронизация каталога Ozon (OZ-2, OZ-5)."""
    import ozon_sync
    result = ozon_sync.sync()
    invalidate_site_cache()
    return jsonify(result)


@app.route('/admin/api/ozon-sync-light', methods=['POST'])
@admin_required
def admin_ozon_sync_light():
    """Лёгкая синхронизация Ozon: цены и остатки (OZ-5)."""
    import ozon_sync
    result = ozon_sync.sync_light()
    invalidate_site_cache()
    return jsonify(result)


def _audit_log(action, object_type='', object_id='', detail=None):
    """Аудит действий админа (AD-4, PR-7): кто/когда/что."""
    from db import db
    from models import AuditLog
    try:
        db.session.add(AuditLog(
            admin_user=session.get('admin_user') or 'admin',
            action=action, object_type=object_type,
            object_id=str(object_id or ''), detail=detail or {},
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


@app.route('/admin/api/wb-sync-log')
@admin_required
def admin_wb_sync_log():
    """Журнал синхронизации: ?source=wb|ozon (OZ-6)."""
    source = request.args.get('source') or None
    return jsonify({'success': True, 'entries': wb_sync.get_sync_log(limit=50, source=source)})


@app.route('/api/wb-products')
def api_wb_products():
    """Public endpoint for WB products (used by catalog pages)."""
    return jsonify({'success': True, 'products': get_products()})


@app.route('/catalog/')
def catalog_page():
    """Единый каталог WB+Ozon (CT-1): фильтры, сортировка, пагинация, поиск (CT-5)."""
    content = load_content()
    page = get_page_by_url(content, '/catalog/')
    if not page:
        return 'Страница не найдена', 404
    # Событие «поисковый запрос» (RS-1)
    q = (request.args.get('q') or '').strip()
    if q:
        try:
            customers.add_event('search', query=q[:500],
                                customer_id=session.get('customer_id'),
                                session_id=_session_id())
        except Exception:
            pass
    ctx = make_context(content, page)
    ctx['catalog_view'] = _filtered_catalog(request.args)
    return render_template(page['template'], **ctx)


def _filtered_catalog(args):
    """Фильтрация/сортировка/пагинация каталога (CT-1, CT-5)."""
    import math as _math
    products = list(get_products())

    q = (args.get('q') or '').strip().lower()
    if q:
        products = [p for p in products if q in (p.get('name') or '').lower()
                    or q in (p.get('brand') or '').lower()
                    or q in str(p.get('vendor_code') or '').lower()
                    or q in str(p.get('nm_id') or '')]
    source = args.get('source') or ''
    if source in ('wb', 'ozon'):
        products = [p for p in products if p.get('source') == source]
    category = args.get('category') or ''
    if category:
        products = [p for p in products if p.get('category') == category]
    if args.get('in_stock'):
        products = [p for p in products if (p.get('stock') or 0) > 0]
    try:
        price_from = int(args.get('price_from') or 0)
        price_to = int(args.get('price_to') or 0)
    except ValueError:
        price_from = price_to = 0
    if price_from:
        products = [p for p in products if (p.get('final_price') or 0) >= price_from]
    if price_to:
        products = [p for p in products if (p.get('final_price') or 0) <= price_to]

    sort = args.get('sort') or 'popular'
    if sort == 'price_asc':
        products.sort(key=lambda p: p.get('final_price') or 0)
    elif sort == 'price_desc':
        products.sort(key=lambda p: p.get('final_price') or 0, reverse=True)
    elif sort == 'new':
        products.sort(key=lambda p: p.get('updated_at') or '', reverse=True)
    else:  # popular — по заказам и лайкам из базы клиентов (RS-2)
        popular = customers.get_popular_products(products, limit=len(products) or 1)
        score_by_key = {(_product_key(e['product'])): e['score'] for e in popular}
        products.sort(key=lambda p: score_by_key.get(_product_key(p), 0), reverse=True)

    per_page = 24
    try:
        page_num = max(1, int(args.get('page') or 1))
    except ValueError:
        page_num = 1
    total = len(products)
    pages = max(1, _math.ceil(total / per_page))
    page_num = min(page_num, pages)
    items = products[(page_num - 1) * per_page: page_num * per_page]

    categories = sorted({p.get('category') for p in get_products() if p.get('category')})
    return {
        'products': items,
        'total': total,
        'page': page_num,
        'pages': pages,
        'categories': categories,
        'applied': {
            'q': args.get('q') or '', 'source': source, 'category': category,
            'in_stock': bool(args.get('in_stock')),
            'price_from': args.get('price_from') or '', 'price_to': args.get('price_to') or '',
            'sort': sort,
        },
    }


def _product_key(p):
    return f"{p.get('source', 'wb')}:{p.get('nm_id') or p.get('id')}"


@app.route('/catalog/wb/<int:nm_id>/')
def wb_product_page(nm_id):
    """Карточка товара WB (CT-2)."""
    return _product_page('wb', nm_id)


@app.route('/catalog/ozon/<int:product_id>/')
def ozon_product_page(product_id):
    """Карточка товара Ozon (CT-2)."""
    return _product_page('ozon', product_id)


def _product_page(source, nm_id):
    """Единая карточка товара для обоих источников (CT-2…CT-4, OZ-3)."""
    product = next((p for p in get_products()
                    if p.get('source') == source and int(p.get('nm_id') or 0) == nm_id), None)
    if not product:
        return 'Товар не найден', 404
    content = load_content()

    # Событие «просмотр карточки» (RS-1) — для рекомендаций.
    try:
        customers.add_event('view', product_id=product.get('nm_id'),
                            product_name=product.get('name', ''),
                            customer_id=session.get('customer_id'),
                            session_id=_session_id())
    except Exception:
        pass

    # SEO title/description из шаблонов с переменными {name} {brand} {price}
    seo = content.get('site', {}).get('seo', {})
    values = {
        'name': product.get('name', ''),
        'brand': product.get('brand') or 'Экотория',
        'price': product.get('final_price') or product.get('price') or 0,
    }

    def _format(tpl):
        try:
            return str(tpl or '').format(**values)
        except (KeyError, ValueError):
            return str(tpl or '')

    page = dict(get_page_by_slug(content, 'catalog__namatrasniki'))
    page['title'] = _format(seo.get('product_title_template')) or page.get('title', '')
    page['meta_description'] = _format(seo.get('product_description_template')) or page.get('meta_description', '')

    ctx = make_context(content, page)
    ctx['seo_product'] = _seo_product(product, request.host_url)
    ctx['seo_og_image'] = _absolute_url(product.get('photo'), request.host_url) or ctx['seo_og_image']
    return render_template('pages/catalog__namatrasniki.html', **ctx, wb_product=product)


def _seo_product(product, host_url):
    """Product (JSON-LD) для карточки товара WB."""
    stock = product.get('stock', 0)
    data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': product.get('name', ''),
        'brand': {'@type': 'Brand', 'name': product.get('brand') or 'Экотория'},
        'offers': {
            '@type': 'Offer',
            'price': product.get('final_price') or product.get('price') or 0,
            'priceCurrency': 'RUB',
            'availability': 'https://schema.org/InStock' if stock and stock > 0 else 'https://schema.org/OutOfStock',
            'url': product.get('url') or '',
        },
    }
    image = _absolute_url(product.get('photo'), host_url)
    if image:
        data['image'] = image
    rating = product.get('rating') or product.get('review_rating')
    if rating:
        try:
            data['aggregateRating'] = {
                '@type': 'AggregateRating',
                'ratingValue': float(rating),
                'reviewCount': int(product.get('review_count') or product.get('ratings_count') or 1),
            }
        except (TypeError, ValueError):
            pass
    return data


@app.route('/api/register', methods=['POST'])
def api_register():
    """Register a new customer (or update existing by phone)."""
    data = request.get_json() or request.form.to_dict() or {}
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Имя и телефон обязательны'}), 400
    consent_pd = bool(data.get('consent_pd'))
    customer = customers.add_customer(name, phone, email, consent_pd=consent_pd)
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
    order = customers.add_order(name, phone, email, product_id, product_name, price,
                                consent_pd=bool(data.get('consent_pd')))
    invalidate_site_cache()
    if request.form.get('redirect'):
        # Обычная (не AJAX) отправка формы из блока конструктора: вернуть на страницу
        return redirect(request.referrer or '/')
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
    customers.add_like(product_id, product_name,
                       customer_id=session.get('customer_id'),
                       session_id=_session_id())
    invalidate_site_cache()
    return jsonify({'success': True, 'likes': customers.get_like_counts().get(product_id, 0)})


@app.route('/api/recommendations')
def api_recommendations():
    """Публичный API рекомендаций (RS-2): popular | personal | similar | also_bought."""
    import recommendations
    kind = request.args.get('kind', 'popular')
    products = get_products()
    limit = request.args.get('limit', type=int) or 8
    limit = max(1, min(48, limit))
    try:
        if kind == 'personal':
            items = recommendations.personal_products(
                products, customer_id=session.get('customer_id'),
                session_id=_session_id(), limit=limit)
        elif kind in ('similar', 'also_bought'):
            source = request.args.get('source', 'wb')
            pid = request.args.get('product_id', type=int) or 0
            product = next((p for p in products
                            if p.get('source') == source
                            and int(p.get('nm_id') or 0) == pid), None)
            if not product:
                return jsonify({'success': False, 'error': 'Товар не найден'}), 404
            fn = (recommendations.similar_products if kind == 'similar'
                  else recommendations.also_bought)
            items = fn(products, product, limit=limit)
        else:
            items = recommendations.popular_products(products, limit=limit)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'kind': kind, 'products': items})


@app.route('/admin/api/customers')
@admin_required
def admin_api_customers():
    """Return all customers for admin panel."""
    return jsonify({'success': True, 'customers': customers.get_customers()})


@app.route('/admin/api/customers/export')
@admin_required
def admin_api_customers_export():
    """Экспорт базы клиентов в CSV (CL-4)."""
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['id', 'name', 'phone', 'email', 'consent_pd',
                     'orders_count', 'total_spent', 'last_activity_at', 'created_at'])
    for c in customers.get_customers():
        stats = customers.get_customer_stats(c['id'])
        writer.writerow([
            c['id'], c['name'], c['phone'], c['email'],
            'да' if c.get('consent_pd') else '',
            stats['orders_count'], stats['total_spent'],
            c.get('last_activity_at', ''), c.get('created_at', ''),
        ])
    resp = app.response_class(
        buf.getvalue().encode('utf-8-sig'),
        mimetype='text/csv; charset=utf-8',
    )
    resp.headers['Content-Disposition'] = 'attachment; filename="customers.csv"'
    _audit_log('customers.export', 'customers', '', {'count': len(customers.get_customers())})
    return resp


@app.route('/admin/api/customer/delete', methods=['POST'])
@admin_required
def admin_api_customer_delete():
    """Удаление клиента по запросу (152-ФЗ, CL-6) — каскадно, с аудитом."""
    data = request.get_json() or {}
    customer_id = data.get('id')
    if not customer_id:
        return jsonify({'success': False, 'error': 'id обязателен'}), 400
    ok = customers.delete_customer(int(customer_id))
    if not ok:
        return jsonify({'success': False, 'error': 'Клиент не найден'}), 404
    _audit_log('customer.delete', 'customer', customer_id)
    return jsonify({'success': True})


@app.route('/admin/api/segments')
@admin_required
def admin_api_segments():
    """Сегменты клиентов (CL-5), расчёт на лету.
    Параметры: days (период, по умолчанию 30), min_orders (порог, по умолчанию 2)."""
    days = request.args.get('days', type=int) or 30
    min_orders = request.args.get('min_orders', type=int) or 2
    days = max(1, min(3650, days))
    min_orders = max(1, min(1000, min_orders))
    segments = customers.compute_segments(get_products(), days=days, min_orders=min_orders)
    return jsonify({'success': True, 'days': days, 'min_orders': min_orders,
                    'segments': segments})


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
    allowed_hosts = ('basket-', 'cdn', 'wildberries.ru', 'wb.ru')
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
            r = requests.get(url, timeout=6, headers={
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


WB_AUTOSYNC_DEFAULTS = {
    'enabled': False,
    'catalog_hours': 6,     # полная синхронизация (карточки + цены + остатки)
    'light_minutes': 30,    # лёгкая (цены + остатки)
}


def get_autosync_settings():
    """Настройки авто-синхронизации WB из content.json (с значениями по умолчанию)."""
    settings = dict(WB_AUTOSYNC_DEFAULTS)
    try:
        saved = load_content().get('site', {}).get('wb_autosync') or {}
        settings.update(saved)
    except Exception:
        pass
    return settings


def start_autosync():
    """Авто-синхронизация перенесена в cron-скрипт jobs/autosync.py (AR-4).

    Оставлено для обратной совместимости команд запуска — синхронизация
    больше не выполняется в процессе веб-сервера.
    """
    print('[autosync] фоновый поток отключён: запускайте `python jobs/autosync.py` по расписанию (cron/планировщик задач)')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

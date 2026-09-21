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
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, Response, send_from_directory

# Static assets with unusual extensions (Bitrix legacy)
mimetypes.add_type('text/javascript', '.php')
mimetypes.add_type('text/css', '.less')

import copy

import customers
import wb_sync
import blocks as blocks_module
import fonts as fonts_module
import page_templates as page_templates_module
import storage
import zones as zones_module
import db as db_module
from db import db
from models import Customer, Order
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

# Миграция «цикл 2» конструктора (CN-T5/6/7/9/10): pages.published_blocks/settings,
# таблицы page_versions/block_templates. Идемпотентна, старт не роняет.
try:
    import migrate_cycle2
    migrate_cycle2.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle2] пропущено: {_migrate_err}')

# Кэширование (AR-3): SimpleCache по умолчанию, тип — через env CACHE_TYPE.
from flask_caching import Cache
cache = Cache(app, config={
    'CACHE_TYPE': os.environ.get('CACHE_TYPE', 'SimpleCache'),
    'CACHE_DEFAULT_TIMEOUT': 300,
})

# Этап 6.1 (2.2.7 ТЗ): HTTPS-only — выключено по умолчанию, включается env FORCE_HTTPS=1
# (за nginx с TLS редиректит на https и выставляет HSTS; /health не трогаем).
FORCE_HTTPS = os.environ.get('FORCE_HTTPS') == '1'


@app.before_request
def force_https_redirect():
    """308-редирект на https при FORCE_HTTPS=1 (кроме /health для проверок)."""
    if not FORCE_HTTPS or request.path == '/health':
        return None
    proto = request.headers.get('X-Forwarded-Proto', request.scheme)
    if proto == 'https':
        return None
    return redirect(request.url.replace('http://', 'https://', 1), code=308)


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

    Этап 6.1 (2.2.7 ТЗ): CSP мягкий — на страницах есть инлайн-скрипты/стили
    (unsafe-inline обязателен), script-src допускает https: (Метрика/GA),
    frame-src — только Яндекс.Карты и YouTube/Vimeo (блок map/video).
    """
    path = request.path
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' https:; "
        "font-src 'self' data:; "
        "connect-src 'self' https:; "
        "frame-src https://yandex.ru https://yastatic.net https://www.youtube.com https://player.vimeo.com; "
        "frame-ancestors 'self'"
    )
    if FORCE_HTTPS:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
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
    """Проверка учётных данных админа (AD-2): сначала таблица admin_users,
    фолбэк — ADMIN_CREDENTIALS (главный суперадмин из файла)."""
    user = _find_admin_user(username)
    if user is not None:
        return check_password_hash(user['password_hash'], password)
    return username == ADMIN_CREDENTIALS.get('user') and check_password_hash(ADMIN_CREDENTIALS['hash'], password)


def _find_admin_user(username):
    """AdminUser по логину + ленивый перенос ADMIN_CREDENTIALS в таблицу (AD-2)."""
    from models import AdminUser
    try:
        row = db.session.query(AdminUser).filter_by(username=username).first()
        if row is None and username == ADMIN_CREDENTIALS.get('user'):
            # Первый запуск: заводим главного админа как суперадмина
            row = AdminUser(
                username=username,
                password_hash=ADMIN_CREDENTIALS['hash'],
                role='superadmin',
                must_change_password=bool(ADMIN_CREDENTIALS.get('must_change_password')),
            )
            db.session.add(row)
            db.session.commit()
        if row is None:
            return None
        return {'username': row.username, 'role': row.role or 'superadmin',
                'password_hash': row.password_hash,
                'must_change_password': bool(row.must_change_password)}
    except Exception:
        db.session.rollback()
        return None


def _admin_role():
    """Роль текущего админа из сессии (AD-2)."""
    role = session.get('admin_role')
    if role:
        return role
    user = session.get('admin_user')
    if not user:
        return None
    found = _find_admin_user(user)
    return found['role'] if found else None


# AD-2: каким ролям какие разделы доступны.
# superadmin — всё; content — контент сайта; orders — заявки/клиенты/товары.
ROLE_SECTIONS = {
    'superadmin': {'pages', 'theme', 'menu', 'socials', 'seo', 'site', 'integrations',
                   'customers', 'backup', 'dashboard', 'audit', 'users'},
    'content': {'pages', 'theme', 'menu', 'socials', 'seo', 'site', 'backup'},
    'orders': {'dashboard', 'customers', 'integrations', 'backup'},
}

# Префикс пути → раздел (проверяется в порядке следования).
ADMIN_PATH_SECTIONS = [
    ('/admin/api/admin-user', 'users'),
    ('/admin/api/dashboard', 'dashboard'),
    ('/admin/api/audit', 'audit'),
    ('/admin/api/backup', 'backup'),
    ('/admin/api/restore', 'backup'),
    ('/admin/api/customer', 'customers'),
    ('/admin/api/customers', 'customers'),
    ('/admin/api/segments', 'customers'),
    ('/admin/api/orders', 'customers'),
    ('/admin/api/order-status', 'customers'),
    ('/admin/page/', 'pages'),
    ('/admin/api/page', 'pages'),
    ('/admin/api/upload', 'pages'),
    ('/admin/api/add-page', 'pages'),
    ('/admin/api/delete-page', 'pages'),
    ('/admin/api/duplicate-page', 'pages'),
    ('/admin/api/render-blocks', 'pages'),
    ('/admin/api/rebuild', 'pages'),
    ('/admin/api/block-templates', 'pages'),
    ('/admin/api/global-zones', 'site'),
    ('/admin/api/fonts', 'theme'),
    ('/admin/api/theme', 'theme'),
    ('/admin/api/seo', 'seo'),
    ('/admin/api/redirects-import', 'seo'),
    ('/admin/api/seo-audit', 'seo'),
    ('/admin/api/wb-products', 'integrations'),
    ('/admin/api/product-discount', 'integrations'),
    ('/admin/api/discount', 'integrations'),
    ('/admin/api/export-settings', 'integrations'),
    ('/admin/api/wb-sync', 'integrations'),
    ('/admin/api/ozon', 'integrations'),
    ('/admin/api/wb-token', 'integrations'),
    ('/admin/api/wb-autosync', 'integrations'),
    ('/admin/api/save', 'site'),
    ('/admin/api/full-content', 'backup'),
    ('/admin/api/recent-uploads', 'pages'),
    ('/admin/', 'dashboard'),
]


def _section_for_path(path):
    for prefix, section in ADMIN_PATH_SECTIONS:
        if path.startswith(prefix):
            return section
    return None


def _role_allows(role, section):
    if not section:
        return True
    return section in ROLE_SECTIONS.get(role or '', set())


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


def _rate_limit(max_per_minute=20):
    """Простой in-memory rate-limit публичных POST (Этап 6.1, 2.2.7 ТЗ).

    Счётчик — deque временных меток по ключу (ip, endpoint) в памяти
    процесса, без внешних зависимостей. При превышении — 429.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            key = (request.remote_addr or '?', request.endpoint or request.path)
            with _rate_lock:
                bucket = _rate_buckets.get(key)
                if bucket is None:
                    bucket = _rate_buckets[key] = deque()
                while bucket and now - bucket[0] > 60:
                    bucket.popleft()
                if len(bucket) >= max_per_minute:
                    return jsonify({'success': False,
                                    'error': 'Слишком много запросов. Повторите через минуту'}), 429
                bucket.append(now)
                # Раз в ~5000 ключей вычищаем давно пустые корзины
                if len(_rate_buckets) > 5000:
                    stale = [k for k, b in _rate_buckets.items() if not b or now - b[-1] > 3600]
                    for k in stale:
                        _rate_buckets.pop(k, None)
            return f(*args, **kwargs)
        return wrapper
    return decorator


_rate_lock = threading.Lock()
_rate_buckets = {}

# Lockout входа в админку (Этап 6.1): 5 неудачных попыток подряд для
# пары (логин, IP) → блокировка на 15 минут. In-memory — для одного
# процесса; при мультисерверном деплое счётчики не общие (см. WORKLOG).
LOGIN_MAX_ATTEMPTS = 5
LOGIN_BLOCK_SECONDS = 15 * 60
_login_lock = threading.Lock()
_login_attempts = {}   # (username, ip) -> {'count': int, 'blocked_until': float}


def _login_blocked_left(username):
    """Остаток блокировки в минутах для пары (логин, IP); 0 — не заблокирован."""
    key = (str(username or '').lower(), request.remote_addr or '?')
    with _login_lock:
        rec = _login_attempts.get(key)
        if not rec:
            return 0
        left = rec.get('blocked_until', 0) - time.monotonic()
        return -(-int(left) // 60) if left > 0 else 0   # ceil до минут


def _login_fail(username):
    """Зафиксировать неудачную попытку; при 5-й подряд — блокировка на 15 минут."""
    key = (str(username or '').lower(), request.remote_addr or '?')
    with _login_lock:
        rec = _login_attempts.setdefault(key, {'count': 0, 'blocked_until': 0})
        rec['count'] += 1
        if rec['count'] >= LOGIN_MAX_ATTEMPTS:
            rec['blocked_until'] = time.monotonic() + LOGIN_BLOCK_SECONDS


def _login_success(username):
    """Сброс счётчика неудачных попыток после успешного входа."""
    key = (str(username or '').lower(), request.remote_addr or '?')
    with _login_lock:
        _login_attempts.pop(key, None)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_user'):
            if request.path.startswith('/admin/api/'):
                return jsonify({'success': False, 'error': 'Требуется авторизация'}), 401
            return redirect(url_for('admin_login', next=request.path))
        # AD-2: роль должна иметь доступ к разделу этого пути
        section = _section_for_path(request.path)
        if not _role_allows(_admin_role(), section):
            if request.path.startswith('/admin/api/'):
                return jsonify({'success': False, 'error': 'Недостаточно прав для этого раздела'}), 403
            return 'Недостаточно прав для этого раздела', 403
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
        blocked_min = _login_blocked_left(username)
        if blocked_min:
            error = f'Слишком много попыток. Повторите через {blocked_min} минут'
        elif check_auth(username, password):
            _login_success(username)
            session['admin_user'] = username
            session.pop('csrf_token', None)  # новый токен для новой сессии
            found = _find_admin_user(username)
            session['admin_role'] = found['role'] if found else 'superadmin'
            if ADMIN_CREDENTIALS.get('must_change_password'):
                return redirect(url_for('admin_change_password'))
            return redirect(request.args.get('next') or url_for('admin'))
        else:
            _login_fail(username)
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
    # Google-шрифты из курируемого списка fonts.py (CN-T7.2); '' = не задан
    'heading_font_google': '',
    'body_font_google': '',
    # Кегли заголовков (CN-T7)
    'h1_size': '2.5rem',
    'h2_size': '2rem',
    'h3_size': '1.6rem',
    'h4_size': '1.3rem',
    'h5_size': '1.1rem',
    'h6_size': '1rem',
    # Интерлиньяж (без единиц)
    'base_line_height': '1.6',
    'heading_line_height': '1.2',
    # Кнопки: тень 0|1|2, вариант fill|outline
    'button_shadow': '0',
    'button_variant': 'fill',
    'version': 1,
}

SEO_DEFAULTS = {
    'yandex_metrika_id': '96433627',
    'google_analytics_id': '',
    'robots_txt': 'User-agent: *\nDisallow: /admin\nAllow: /\n',
    'extra_head': '',
    'product_title_template': '{name} — купить от {price} ₽ | {site_name}',
    'product_description_template': '{name}. Бренд {brand}. Цена {price} ₽{discount}',
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


def _render_meta_template(template, product, site):
    """Шаблон мета карточки товара (SEO-2): подстановка {name} {brand} {price}
    {category} {discount} {site_name}. Неизвестные/битые переменные не роняют
    рендер — возвращаем шаблон как есть."""
    price = product.get('final_price') or product.get('price') or 0
    try:
        price = int(price)
    except (TypeError, ValueError):
        price = 0
    discount = ''
    try:
        base = int(product.get('price') or 0)
        final = int(product.get('final_price') or 0)
    except (TypeError, ValueError):
        base = final = 0
    if final and base and final < base:
        # {discount} — «, скидка N%» по разнице price → final_price (SEO-2)
        discount = f', скидка {round((base - final) * 100 / base)}%'
    values = {
        'name': product.get('name', ''),
        'brand': product.get('brand') or 'Экотория',
        'price': price,
        'category': product.get('category') or '',
        'discount': discount,
        'site_name': site.get('name') or site.get('title') or 'Экотория',
    }
    try:
        return str(template or '').format(**values)
    except (KeyError, ValueError, IndexError):
        return str(template or '')


def _seo_breadcrumbs(content, page, host_url):
    """Хлебные крошки (SEO-4): Главная → …уровни URL страницы.
    Названия уровней — title соответствующих страниц, иначе сегмент пути."""
    crumbs = [{'name': 'Главная', 'url': host_url}]
    url = (page.get('url') or '/').strip('/')
    if not url:
        return crumbs
    parts = url.split('/')
    for i in range(1, len(parts) + 1):
        path = '/' + '/'.join(parts[:i]) + '/'
        parent = get_page_by_url(content, path)
        name = (parent.get('title') or parent.get('h1') or parts[i - 1]) if parent else parts[i - 1]
        crumbs.append({'name': name, 'url': host_url.rstrip('/') + path})
    return crumbs


def _seo_faq_items(page):
    """Вопросы-ответы блоков faq страницы (SEO-4) — для JSON-LD FAQPage."""
    items = []
    blocks = page.get('blocks')
    if not isinstance(blocks, list):
        return items
    for b in blocks:
        if not isinstance(b, dict) or b.get('type') != 'faq':
            continue
        raw = b.get('items')
        if not isinstance(raw, list):
            continue
        for it in raw:
            if not isinstance(it, dict):
                continue
            q = str(it.get('q') or '').strip()
            a = str(it.get('a') or '').strip()
            if q and a:
                items.append({'q': q, 'a': a})
    return items


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
        'seo_breadcrumbs': _seo_breadcrumbs(content, page, host_url),
        'seo_faq': _seo_faq_items(page),
        # canonical: поле страницы в приоритете, иначе — абсолютный URL текущей
        # страницы (SEO-1). Для карточки товара _product_page переопределяет.
        'seo_canonical': _absolute_url(page.get('canonical'), host_url)
                         or (host_url.rstrip('/') + request.path),
        'seo_product': None,
    }
    # Глобальные зоны шапка/подвал (CN-T6): страница может переопределять конфиг
    # через page.settings.override_header/footer (+ header_config/footer_config).
    try:
        page_settings = page.get('settings') if isinstance(page.get('settings'), dict) else {}
        zones_cfg = zones_module.get_zones(content)
        if page_settings.get('override_header') and isinstance(page_settings.get('header_config'), dict):
            header_cfg = {**zones_module.HEADER_DEFAULTS,
                          **page_settings['header_config'], 'use_global': True}
        else:
            header_cfg = zones_cfg.get('header')
        if page_settings.get('override_footer') and isinstance(page_settings.get('footer_config'), dict):
            footer_cfg = {**zones_module.FOOTER_DEFAULTS,
                          **page_settings['footer_config'], 'use_global': True}
        else:
            footer_cfg = zones_cfg.get('footer')
        ctx['zone_header_html'] = zones_module.render_zone('header', header_cfg, content)
        ctx['zone_footer_html'] = zones_module.render_zone('footer', footer_cfg, content)
        ctx['zones_css'] = zones_module.zones_css(header_cfg) if ctx['zone_header_html'] else ''
    except Exception:
        ctx['zone_header_html'] = ''
        ctx['zone_footer_html'] = ''
        ctx['zones_css'] = ''
    # Текущий клиент (LC-2): шаблоны показывают «ЛК/Выйти» вместо «Войти».
    cid = session.get('customer_id')
    if cid:
        row = db.session.get(Customer, cid)
        if row is not None:
            ctx['current_customer'] = row.to_dict()
        else:
            session.pop('customer_id', None)
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
    page = get_page_by_slug(content, 'index') or get_page_by_url(content, '/')
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


def _cart_items():
    """Корзина из сессии: [{key, product, qty, price}] (LC-4)."""
    cart = session.get('cart') or {}
    products = get_products()
    by_key = {f"{p.get('source', 'wb')}:{p.get('nm_id') or p.get('id')}": p
              for p in products}
    items, total = [], 0
    for key, qty in cart.items():
        p = by_key.get(key)
        if p is None:
            continue
        qty = max(1, int(qty or 1))
        price = int(p.get('final_price') or p.get('price') or 0)
        items.append({'key': key, 'product': p, 'qty': qty, 'price': price})
        total += price * qty
    return items, total


# Системные страницы (CN-6): их контентные блоки живут в content['pages']
# и редактируются тем же редактором «на странице», что и обычные страницы.
SYSTEM_PAGE_SLUGS = {
    'basket': '/basket/',
    'personal': '/personal/',
    'auth': '/auth/',
    '404': '/404/',
}


def _system_page(slug):
    """Найти системную страницу; при первом обращении — создать запись."""
    content = load_content()
    page = get_page_by_slug(content, slug)
    if page:
        return content, page
    page = {
        'slug': slug,
        'url': SYSTEM_PAGE_SLUGS[slug],
        'title': slug,
        'h1': '',
        'meta_description': '',
        'template': '',
        'blocks': [],
    }
    content['pages'].append(page)
    save_content(content)
    return content, page


def _system_page_blocks_html(slug, marked=False):
    """Отрендеренные блоки системной страницы (сверху её шаблона)."""
    _content, page = _system_page(slug)
    blocks = page.get('blocks')
    if not isinstance(blocks, list):
        blocks = []
    if marked:
        return blocks_module.render_blocks_marked(blocks)
    return blocks_module.render_blocks(blocks)


def _system_edit_mode(slug):
    """Режим ?edit для системной страницы: только админ с разделом 'pages'."""
    if 'edit' not in request.args:
        return False
    if not session.get('admin_user'):
        return False
    return _role_allows(_admin_role(), 'pages')


@app.route('/basket/')
def basket_page():
    """Корзина (LC-4): список товаров, количество, оформление заявкой без оплаты."""
    content = load_content()
    items, total = _cart_items()
    customer = None
    cid = session.get('customer_id')
    if cid:
        row = db.session.get(Customer, cid)
        if row is not None:
            customer = row.to_dict()
    edit_mode = _system_edit_mode('basket')
    html = render_template('basket.html', site=content.get('site', {}),
                           menu=content.get('menu', []), items=items,
                           total=total, customer=customer,
                           blocks_html=_system_page_blocks_html('basket', marked=edit_mode))
    if edit_mode:
        html = _inject_editor(html, 'basket')
    return Response(html)


@app.route('/api/cart/add', methods=['POST'])
def api_cart_add():
    """Добавить товар в корзину (LC-4)."""
    data = request.get_json() or request.form.to_dict() or {}
    source = data.get('source') or 'wb'
    pid = str(data.get('product_id') or data.get('nm_id') or '').strip()
    if not pid:
        return jsonify({'success': False, 'error': 'product_id обязателен'}), 400
    try:
        qty = max(1, int(data.get('qty') or 1))
    except (TypeError, ValueError):
        qty = 1
    key = f'{source}:{pid}'
    cart = session.get('cart') or {}
    cart[key] = int(cart.get(key, 0)) + qty
    session['cart'] = cart
    session.modified = True
    return jsonify({'success': True, 'cart': cart, 'count': sum(cart.values())})


@app.route('/api/cart/update', methods=['POST'])
def api_cart_update():
    """Изменить количество / удалить позицию (qty=0 — удалить)."""
    data = request.get_json() or {}
    key = str(data.get('key') or '').strip()
    cart = session.get('cart') or {}
    if key not in cart:
        return jsonify({'success': False, 'error': 'Позиция не найдена'}), 404
    try:
        qty = int(data.get('qty') or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        cart.pop(key, None)
    else:
        cart[key] = qty
    session['cart'] = cart
    session.modified = True
    return jsonify({'success': True, 'cart': cart, 'count': sum(cart.values())})


@app.route('/api/cart/checkout', methods=['POST'])
@_rate_limit(20)
def api_cart_checkout():
    """Оформление корзины заявкой без оплаты (LC-4).

    Каждая позиция → заявка source='basket' в базе админки. При совпадении
    телефона с аккаунтом заявки привязываются к нему (LC-5).
    """
    data = request.get_json() or request.form.to_dict() or {}
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    if not name or not phone:
        return jsonify({'success': False, 'error': 'Имя и телефон обязательны'}), 400
    items, _total = _cart_items()
    if not items:
        return jsonify({'success': False, 'error': 'Корзина пуста'}), 400
    orders = []
    for it in items:
        p = it['product']
        order = customers.add_order(
            name, phone, email, p.get('nm_id') or p.get('id'), p.get('name', ''),
            it['price'], qty=it['qty'], source='basket',
            consent_pd=bool(data.get('consent_pd')))
        orders.append(order)
    session['cart'] = {}
    session.modified = True
    invalidate_site_cache()
    return jsonify({'success': True, 'orders': orders})


def _client_required(f):
    """Только для залогиненного клиента (LC-2): иначе 401 (API) / редирейт на /auth/."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('customer_id'):
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Требуется вход'}), 401
            return redirect(url_for('auth_page', next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route('/personal/')
@app.route('/personal/favorite/')
@app.route('/personal/orders/')
@_client_required
def personal_page():
    """Личный кабинет (LC-3): контакты, заявки со статусами, избранное,
    персональные рекомендации. Маршруты favorite/orders — для старых ссылок."""
    content = load_content()
    customer = db.session.get(Customer, session['customer_id']).to_dict()
    orders = customers.get_customer_orders(customer['id'])
    likes = customers.get_customer_likes(customer['id'])
    stats = customers.get_customer_stats(customer['id'])
    # Избранное с полными карточками товаров
    products = get_products()
    liked_ids = {str(l['product_id']) for l in likes}
    favorite_products = [p for p in products
                         if str(p.get('nm_id') or p.get('id')) in liked_ids]
    try:
        import recommendations
        personal = recommendations.personal_products(
            products, customer_id=customer['id'], limit=8)
    except Exception:
        personal = []
    edit_mode = _system_edit_mode('personal')
    html = render_template('personal.html', site=content.get('site', {}),
                           menu=content.get('menu', []), customer=customer,
                           orders=orders, likes=likes, stats=stats,
                           favorite_products=favorite_products,
                           personal_products=_rec_block_items(personal),
                           blocks_html=_system_page_blocks_html('personal', marked=edit_mode))
    if edit_mode:
        html = _inject_editor(html, 'personal')
    return Response(html)


@app.route('/api/client/profile', methods=['POST'])
@_client_required
def api_client_profile():
    """Обновление контактов клиента из ЛК (LC-3)."""
    data = request.get_json() or {}
    updated = customers.update_customer_profile(
        session['customer_id'], name=data.get('name'), email=data.get('email'))
    if not updated:
        return jsonify({'success': False, 'error': 'Клиент не найден'}), 404
    return jsonify({'success': True, 'customer': updated})


@app.route('/auth/')
def auth_page():
    """Страница входа/регистрации клиента (LC-1)."""
    if session.get('customer_id'):
        return redirect(url_for('personal_page'))
    content = load_content()
    edit_mode = _system_edit_mode('auth')
    html = render_template('auth.html', site=content.get('site', {}),
                           menu=content.get('menu', []), error=None,
                           blocks_html=_system_page_blocks_html('auth', marked=edit_mode))
    if edit_mode:
        html = _inject_editor(html, 'auth')
    return Response(html)


@app.route('/api/client/register', methods=['POST'])
@_rate_limit(20)
def api_client_register():
    """Регистрация клиента: телефон + пароль (LC-1)."""
    data = request.get_json() or request.form.to_dict() or {}
    customer, error = customers.register_client(
        phone=data.get('phone', '').strip(),
        password=data.get('password', ''),
        name=data.get('name', '').strip(),
        email=data.get('email', '').strip())
    if error:
        return jsonify({'success': False, 'error': error}), 400
    if data.get('remember'):
        session.permanent = True
    session['customer_id'] = customer['id']
    return jsonify({'success': True, 'customer': customer})


@app.route('/api/client/login', methods=['POST'])
@_rate_limit(20)
def api_client_login():
    """Вход клиента по телефону+паролю (LC-1, LC-2)."""
    data = request.get_json() or request.form.to_dict() or {}
    customer = customers.login_client(data.get('phone', '').strip(),
                                      data.get('password', ''))
    if not customer:
        return jsonify({'success': False, 'error': 'Неверный телефон или пароль'}), 401
    if data.get('remember'):
        session.permanent = True
    session['customer_id'] = customer['id']
    return jsonify({'success': True, 'customer': customer})


@app.route('/api/client/logout', methods=['POST'])
def api_client_logout():
    """Выход клиента (LC-2)."""
    session.pop('customer_id', None)
    return jsonify({'success': True})


def _css_color(value, default):
    """Безопасный HEX-цвет для theme.css (защита от подстановки CSS-кода)."""
    value = str(value or '').strip()
    return value if re.fullmatch(r'#[0-9a-fA-F]{3,8}', value) else default


def _css_size(value, default):
    """Безопасный CSS-размер: число + px/rem/em/%/vw/vh (без скобок и точек с запятой)."""
    value = str(value or '').strip()
    return value if re.fullmatch(r'\d+(\.\d+)?(px|rem|em|%|vw|vh)', value) else default


# Ключи темы, валидируемые по типу (CN-3: пресеты принимаем только с валидными значениями).
_THEME_COLOR_KEYS = ('primary_color', 'secondary_color', 'accent_color', 'text_color',
                     'bg_color', 'card_bg', 'button_text_color', 'link_color')
_THEME_SIZE_KEYS = ('font_size', 'border_radius', 'container_width',
                    'h1_size', 'h2_size', 'h3_size', 'h4_size', 'h5_size', 'h6_size')
# Интерлиньяж — число без единиц (CN-T7)
_THEME_LINE_HEIGHT_KEYS = ('base_line_height', 'heading_line_height')
# Перечисления (CN-T7)
_THEME_ENUM_KEYS = {'button_shadow': ('0', '1', '2'), 'button_variant': ('fill', 'outline')}
# Google-шрифты — только из курируемого вайтлиста fonts.py (CN-T7.2)
_THEME_GOOGLE_FONT_KEYS = ('heading_font_google', 'body_font_google')


def _validated_theme(raw):
    """Оставить только известные ключи темы с безопасными значениями.

    Правила те же, что в /theme.css: цвета — HEX, размеры — число+единица,
    шрифты — без скобок/точек с запятой, интерлиньяж — число, enum — строго из
    списка допустимых, google-шрифты — только из вайтлиста fonts.py. None, если
    не получилось собрать тему.
    """
    if not isinstance(raw, dict):
        return None
    theme = {}
    for k in THEME_DEFAULTS:
        if k not in raw:
            continue
        v = raw[k]
        if k in _THEME_COLOR_KEYS:
            theme[k] = _css_color(v, THEME_DEFAULTS[k])
        elif k in _THEME_SIZE_KEYS:
            theme[k] = _css_size(v, THEME_DEFAULTS[k])
        elif k in _THEME_LINE_HEIGHT_KEYS:
            v = str(v or '').strip()
            if re.fullmatch(r'\d+(\.\d+)?', v):
                theme[k] = v
        elif k in _THEME_ENUM_KEYS:
            v = str(v or '').strip()
            if v in _THEME_ENUM_KEYS[k]:
                theme[k] = v
        elif k in _THEME_GOOGLE_FONT_KEYS:
            v = str(v or '').strip()
            if not v or fonts_module.is_valid_family(v):
                theme[k] = v
        elif k == 'version':
            try:
                theme[k] = int(v)
            except (TypeError, ValueError):
                pass
        else:  # font_family, heading_font_family
            v = str(v or '').strip()
            if re.fullmatch(r'[^{}<>;]+', v):
                theme[k] = v
    return theme or None


_BUTTON_SHADOW_CSS = {
    '0': 'none',
    '1': '0 2px 6px rgba(0,0,0,0.15)',
    '2': '0 4px 14px rgba(0,0,0,0.25)',
}


def _google_font_stack(family):
    """font-family стек для Google-шрифта из вайтлиста; '' если не из списка."""
    card = fonts_module.find_font(family)
    if card is None:
        return ''
    fallback = 'serif' if card['category'] == 'serif' else 'sans-serif'
    return f"'{card['family']}', {fallback}"


@app.route('/theme.css')
def theme_css():
    """Generate CSS from site theme settings."""
    content = load_content()
    theme = content.get('site', {}).get('theme', {})
    font_family = theme.get('font_family') or THEME_DEFAULTS['font_family']
    if not re.fullmatch(r'[^{}<>;]+', font_family):
        font_family = THEME_DEFAULTS['font_family']
    heading_font = theme.get('heading_font_family') or font_family
    # Google-шрифты (CN-T7.2): @import только для семейств из курируемого списка
    import_lines = []
    seen_fonts = set()
    for key in ('heading_font_google', 'body_font_google'):
        fam = str(theme.get(key) or '').strip()
        if fam and fam not in seen_fonts:
            seen_fonts.add(fam)
            url = fonts_module.import_url(fam)   # '' для чужих семейств (защита от инъекции)
            if url:
                import_lines.append(f"@import url('{url}');")
    import_css = '\n'.join(import_lines) + '\n' if import_lines else ''
    body_google = _google_font_stack(theme.get('body_font_google'))
    body_family = body_google or font_family
    heading_google = _google_font_stack(theme.get('heading_font_google'))
    heading_family = heading_google or heading_font
    base_lh = str(theme.get('base_line_height') or THEME_DEFAULTS['base_line_height'])
    if not re.fullmatch(r'\d+(\.\d+)?', base_lh):
        base_lh = THEME_DEFAULTS['base_line_height']
    heading_lh = str(theme.get('heading_line_height') or THEME_DEFAULTS['heading_line_height'])
    if not re.fullmatch(r'\d+(\.\d+)?', heading_lh):
        heading_lh = THEME_DEFAULTS['heading_line_height']
    button_shadow = _BUTTON_SHADOW_CSS.get(str(theme.get('button_shadow')), 'none')
    button_variant = str(theme.get('button_variant') or 'fill')
    if button_variant not in ('fill', 'outline'):
        button_variant = 'fill'
    button_variant_css = (
        'background:transparent;color:var(--theme-primary);border:2px solid var(--theme-primary);'
        if button_variant == 'outline' else '')
    head_sizes = {n: _css_size(theme.get(f'h{n}_size'), THEME_DEFAULTS[f'h{n}_size'])
                  for n in range(1, 7)}
    css = f"""{import_css}:root {{
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
    --theme-h1: {head_sizes[1]};
    --theme-h2: {head_sizes[2]};
    --theme-h3: {head_sizes[3]};
    --theme-h4: {head_sizes[4]};
    --theme-h5: {head_sizes[5]};
    --theme-h6: {head_sizes[6]};
    --theme-line-height: {base_lh};
    --theme-heading-line-height: {heading_lh};
    --theme-button-shadow: {button_shadow};
    --theme-button-variant: {button_variant};
}}
body {{
    font-family: {body_family};
    font-size: {_css_size(theme.get('font_size'), '16px')};
    line-height: var(--theme-line-height);
    color: var(--theme-text);
    background-color: var(--theme-bg);
}}
header, .header, .top-block-wrapper, .top_blocks, .front .top_blocks, .footer {{
    font-family: {body_family};
}}
h1, h2, h3, h4, h5, h6, .h1, .h2, .h3 {{
    font-family: {heading_family};
    line-height: var(--theme-heading-line-height);
}}
h1 {{ font-size: var(--theme-h1); }}
h2 {{ font-size: var(--theme-h2); }}
h3 {{ font-size: var(--theme-h3); }}
h4 {{ font-size: var(--theme-h4); }}
h5 {{ font-size: var(--theme-h5); }}
h6 {{ font-size: var(--theme-h6); }}
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
    box-shadow: var(--theme-button-shadow);
    {button_variant_css}
}}
.cb-banner-btn, .cb-timer-btn, .zone-cta {{
    box-shadow: var(--theme-button-shadow);
    {button_variant_css}
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
/* Таймер акции (CN-2) */
.cb-timer { padding: 2rem 0; text-align: center; }
.cb-timer h2 { font-size: 1.6rem; margin-bottom: 1rem; }
.cb-timer-widget { margin: 0.5rem 0 1.2rem; }
.cb-timer-countdown { display: inline-block; font-size: 2rem; font-weight: 700; letter-spacing: 1px; color: #c00; font-variant-numeric: tabular-nums; }
.cb-timer-btn { display: inline-block; background: #b49d84; color: #fff; padding: 0.7rem 1.8rem; border-radius: 6px; text-decoration: none; font-weight: 600; }
.cb-timer-btn:hover { opacity: 0.9; }
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
    # Приоритеты зафиксированы правилом по URL (SEO-3, не настраиваются):
    # главная 1.0, /catalog/ 0.8, остальные страницы 0.5, карточки товаров 0.6.
    def _priority(url):
        if url.rstrip('/') == '':
            return '1.0'
        if url.rstrip('/') == '/catalog':
            return '0.8'
        return '0.5'

    for page in content.get('pages', []):
        if page.get('slug') in SYSTEM_PAGE_SLUGS:
            continue   # системные страницы — свои роуты, в sitemap не нужны
        lastmod = str(page.get('updated_at') or '')[:10]   # ISO date
        lm = f'<lastmod>{lastmod}</lastmod>' if lastmod else ''
        xml.append(f'<url><loc>{site_url}{page["url"]}</loc>{lm}'
                   f'<priority>{_priority(page["url"])}</priority></url>\n')
    for p in get_products():
        nm = p.get('nm_id') or p.get('id')
        if not nm:
            continue
        lastmod = str(p.get('updated_at') or '')[:10]
        lm = f'<lastmod>{lastmod}</lastmod>' if lastmod else ''
        xml.append(f'<url><loc>{site_url}/catalog/{p.get("source", "wb")}/{nm}/</loc>{lm}'
                   f'<priority>0.6</priority></url>\n')
    xml.append('</urlset>')
    return Response(''.join(xml), mimetype='application/xml')


def _redirect_path_ok(path):
    """Валидация пути редиректа (SEO-3): начинается с '/', без пробелов и запятых."""
    return bool(re.fullmatch(r'/[^,\s]*', str(path or '')))


@app.route('/admin/api/redirects-import', methods=['POST'])
@admin_required
def admin_redirects_import():
    """Массовый импорт 301-редиректов из CSV-текста (SEO-3).

    Принимает JSON {text}: по строке на редирект «old_path,new_path»
    (поддерживаются разделители , ; и →). Дубликаты и невалидные строки
    пропускаются; валидные добавляются к site.seo.redirects.
    """
    payload = request.get_json() or {}
    text = str(payload.get('text') or '')
    content = load_content()
    seo = content.setdefault('site', {}).setdefault('seo', {})
    redirects = seo.get('redirects')
    if not isinstance(redirects, dict):
        redirects = {}
    added = skipped = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if '→' in line:
            parts = line.split('→')
        elif ';' in line:
            parts = line.split(';')
        else:
            parts = line.split(',')
        if len(parts) != 2:
            skipped += 1
            continue
        src, dst = parts[0].strip(), parts[1].strip()
        if not _redirect_path_ok(src) or not _redirect_path_ok(dst):
            skipped += 1
            continue
        if src in redirects:
            skipped += 1
            continue
        redirects[src] = dst
        added += 1
    seo['redirects'] = redirects
    if added:
        save_content(content)
        invalidate_site_cache()
    return jsonify({'success': True, 'added': added, 'skipped': skipped,
                    'redirects': redirects})


# Поля блоков, в которых ищем ссылки для аудита (SEO-8).
_AUDIT_LINK_KEYS = ('url', 'link', 'href', 'src', 'image', 'icon', 'photo', 'button_link')


def _collect_block_links(value, links):
    """Рекурсивно собрать строки-ссылки из полей блоков страницы."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _AUDIT_LINK_KEYS and isinstance(v, str):
                links.append(v.strip())
            else:
                _collect_block_links(v, links)
    elif isinstance(value, list):
        for item in value:
            _collect_block_links(item, links)


@app.route('/admin/api/seo-audit')
@admin_required
def admin_seo_audit():
    """SEO-аудит страниц (SEO-8): пустые description/h1, дубли title, битые ссылки.

    Битой считаем локальную ссылку (начинается с '/', не /static/, не /catalog/),
    не входящую в известные URL: страницы, меню, /basket/ /personal/ /auth/.
    Внешние http(s)-ссылки не проверяем.
    """
    content = load_content()
    pages = content.get('pages', [])
    known = {'/'}
    for p in pages:
        u = str(p.get('url') or '').strip()
        if u:
            known.add(u)
    known.update(SYSTEM_PAGE_SLUGS.values())

    def _menu_urls(items):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            u = str(item.get('url') or '').strip()
            if u:
                known.add(u)
            _menu_urls(item.get('children'))

    _menu_urls(content.get('menu'))

    issues = []
    by_title = {}
    for p in pages:
        if not str(p.get('meta_description') or '').strip():
            issues.append({'type': 'no_description', 'page': p.get('slug', ''),
                           'url': p.get('url', ''), 'detail': 'Пустой meta description'})
        if not str(p.get('h1') or '').strip():
            issues.append({'type': 'no_h1', 'page': p.get('slug', ''),
                           'url': p.get('url', ''), 'detail': 'Пустой H1'})
        title = str(p.get('title') or '').strip()
        if title:
            by_title.setdefault(title, []).append(p)
    for title, group in by_title.items():
        if len(group) < 2:
            continue
        for p in group:
            others = ', '.join(g.get('slug', '') for g in group if g is not p)
            issues.append({'type': 'duplicate_title', 'page': p.get('slug', ''),
                           'url': p.get('url', ''),
                           'detail': f'Название «{title}» совпадает со страницами: {others}'})
    for p in pages:
        links = []
        _collect_block_links(p.get('blocks') or [], links)
        seen = set()
        for link in links:
            if not link or link in seen:
                continue
            seen.add(link)
            if link.startswith(('http://', 'https://', 'mailto:', 'tel:', '#')):
                continue   # внешние ссылки не проверяем
            if not link.startswith('/'):
                continue
            if link.startswith('/static/') or link.startswith('/catalog/'):
                continue   # статика и карточки/разделы каталога
            path = link.split('?')[0].split('#')[0]
            if not path.endswith('/'):
                path += '/'
            if path not in known:
                issues.append({'type': 'broken_link', 'page': p.get('slug', ''),
                               'url': p.get('url', ''),
                               'detail': f'Ссылка «{link}» ведёт на несуществующую страницу'})
    return jsonify({'success': True, 'issues': issues})


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
    # Try direct URL match first (системные страницы обслуживаются своими роутами)
    url = '/' + path + '/'
    page = get_page_by_url(content, url)
    if page and page.get('slug') not in SYSTEM_PAGE_SLUGS:
        return render_template(page['template'], **make_context(content, page))
    # Try slug match (double underscores represent slashes)
    slug = path.replace('/', '__').replace('-', '_')
    page = get_page_by_slug(content, slug)
    if page and page.get('slug') not in SYSTEM_PAGE_SLUGS:
        return render_template(page['template'], **make_context(content, page))
    # Fallback: if a parent path exists (e.g. product detail link under /catalog/namatrasniki/),
    # redirect to the nearest parent page to avoid 404s from original product cards.
    parts = path.split('/')
    for i in range(len(parts) - 1, 0, -1):
        parent_url = '/' + '/'.join(parts[:i]) + '/'
        parent_page = get_page_by_url(content, parent_url)
        if parent_page:
            return redirect(parent_url, code=302)
    return page_not_found(None), 404


@app.errorhandler(404)
def page_not_found(e):
    """404 (CN-6): шаблон с редактируемыми блоками системной страницы '404'."""
    content = load_content()
    edit_mode = _system_edit_mode('404')
    html = render_template('pages/404.html', site=content.get('site', {}),
                           menu=content.get('menu', []),
                           blocks_html=_system_page_blocks_html('404', marked=edit_mode))
    if edit_mode:
        html = _inject_editor(html, '404')
    return html


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
    return render_template('admin.html', content=slim, csrf_token=get_csrf_token(),
                           admin_role=_admin_role() or 'superadmin')


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


def _inject_editor(html, slug):
    """Инжект CSS/JS редактора «на странице» в готовый HTML (CN-6 — общий для
    /admin/page/<slug> и системных страниц ?edit)."""
    editor_ver = int(os.path.getmtime(os.path.join(BASE_DIR, 'static', 'js', 'page-editor.js')))
    inject = (
        f'<link rel="stylesheet" href="/static/css/page-editor.css?v={editor_ver}">\n'
        f'<script>window.CSRF_TOKEN = "{get_csrf_token()}";</script>\n'
        f'<script src="/static/js/page-editor.js?v={editor_ver}" data-slug="{slug}" defer></script>\n'
    )
    if '</body>' in html:
        return html.replace('</body>', inject + '</body>', 1)
    return html + inject


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
    return Response(_inject_editor(html, slug))


@app.route('/admin/api/theme-presets', methods=['GET', 'POST'])
@admin_required
def admin_theme_presets():
    """Пресеты темы оформления (CN-3): список/сохранить/применить/удалить/сброс."""
    if request.method == 'GET':
        presets = load_content().get('site', {}).get('theme_presets') or {}
        return jsonify({'success': True, 'presets': presets})

    payload = request.get_json() or {}
    action = payload.get('action', '').strip()
    name = str(payload.get('name') or '').strip()[:80]
    content = load_content()
    site = content.setdefault('site', {})
    presets = site.get('theme_presets')
    if not isinstance(presets, dict):
        presets = {}
        site['theme_presets'] = presets

    if action == 'save':
        if not name:
            return jsonify({'success': False, 'error': 'Укажите имя пресета'}), 400
        theme = _validated_theme(payload.get('theme'))
        if theme is None:
            return jsonify({'success': False, 'error': 'Некорректная тема'}), 400
        presets[name] = theme
        save_content(content)
        return jsonify({'success': True})

    if action == 'delete':
        if name not in presets:
            return jsonify({'success': False, 'error': 'Пресет не найден'}), 404
        del presets[name]
        save_content(content)
        return jsonify({'success': True})

    if action == 'apply':
        theme = _validated_theme(presets.get(name))
        if theme is None:
            return jsonify({'success': False, 'error': 'Пресет не найден'}), 404
        # Инвалидируем кэш /theme.css у браузеров, как при обычном сохранении темы
        theme['version'] = int((site.get('theme') or {}).get('version') or 1) + 1
        site['theme'] = theme
        save_content(content)
        invalidate_site_cache()
        return jsonify({'success': True, 'theme': theme})

    if action == 'reset':
        theme = dict(THEME_DEFAULTS)
        theme['version'] = int((site.get('theme') or {}).get('version') or 1) + 1
        site['theme'] = theme
        save_content(content)
        invalidate_site_cache()
        return jsonify({'success': True, 'theme': theme})

    return jsonify({'success': False, 'error': 'action must be save|apply|delete|reset'}), 400


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
        'page': {k: page.get(k, '') for k in ('slug', 'url', 'title', 'h1', 'meta_description', 'meta_keywords', 'canonical')},
        'blocks': blocks,
        'legacy': any(isinstance(b, dict) and b.get('legacy') for b in blocks),
        'updated_at': page.get('updated_at', ''),
    })


@app.route('/admin/api/page-draft', methods=['GET', 'POST'])
@admin_required
def admin_page_draft():
    """Черновики страниц (CN-5/CN-7 → CN-T10.3): хранятся в Page.draft_blocks.

    POST {slug, blocks} — сохранить черновик в колонку страницы;
    GET ?slug= — получить черновик (формат ответа прежний: draft: {blocks, saved_at},
    плюс has_draft, published_at, updated_at). Если в колонке пусто — фолбэк
    на старый формат site_settings.page_drafts (обратная совместимость).
    """
    from models import Page
    if request.method == 'GET':
        slug = request.args.get('slug', '').strip()
        page = db.session.query(Page).filter_by(slug=slug).first() if slug else None
        if page is None:
            return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
        draft = page.draft_blocks if isinstance(page.draft_blocks, dict) else None
        if draft is None:
            drafts = storage.get_setting('page_drafts') or {}
            legacy = drafts.get(slug) if isinstance(drafts, dict) else None
            if isinstance(legacy, dict):
                draft = legacy
        if not isinstance(draft, dict):
            return jsonify({'success': False, 'error': 'Черновик не найден'}), 404
        updated_at = page.updated_at.isoformat(timespec='seconds') if page.updated_at else ''
        return jsonify({
            'success': True,
            'draft': draft,
            'has_draft': True,
            'published_at': updated_at if page.published_blocks is not None else None,
            'updated_at': updated_at,
        })
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    blocks = payload.get('blocks')
    if not slug or not isinstance(blocks, list):
        return jsonify({'success': False, 'error': 'slug и blocks обязательны'}), 400
    page = db.session.query(Page).filter_by(slug=slug).first()
    if page is None:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    saved_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    page.draft_blocks = {'blocks': [b for b in blocks if isinstance(b, dict)],
                         'saved_at': saved_at}
    db.session.commit()
    return jsonify({'success': True, 'saved_at': saved_at})


@app.route('/admin/api/preview-marked', methods=['POST'])
@admin_required
def admin_preview_marked():
    """Отрендерить блоки с маркерами (для мгновенного обновления страницы в редакторе)."""
    payload = request.get_json() or {}
    blocks = payload.get('blocks') or []
    return jsonify({'success': True, 'content_html': blocks_module.render_blocks_marked(blocks)})


@app.route('/admin/api/media')
@admin_required
def admin_media():
    """Медиатека (CN-T12.3): список загруженных файлов static/uploads.

    ?q= — фильтр по имени; ?limit= — лимит (по умолчанию 200, макс. 500).
    Возвращает файлы по убыванию времени изменения: {name, url, size, mtime}.
    """
    import glob as _glob
    q = (request.args.get('q') or '').strip().lower()
    limit = request.args.get('limit', type=int) or 200
    limit = max(1, min(500, limit))
    upload_dir = BASE_DIR / 'static' / 'uploads'
    files = []
    if upload_dir.is_dir():
        for path in upload_dir.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(upload_dir).as_posix()
            if q and q not in rel.lower():
                continue
            stat = path.stat()
            files.append({'name': rel,
                          'url': f'/static/uploads/{rel}',
                          'size': stat.st_size,
                          'mtime': stat.st_mtime})
    files.sort(key=lambda f: -f['mtime'])
    return jsonify({'success': True, 'files': files[:limit]})


@app.route('/admin/api/page-save', methods=['POST'])
@admin_required
def admin_page_save():
    """Сохранить одну страницу (мета + блоки) из редактора на странице.

    CN-5 (конфликт админов): если передан expected_updated_at и он не совпадает
    с текущим updated_at страницы — отказ 409, чтобы второй админ не затирал
    изменения первого. Повторный save без expected_updated_at перезаписывает.
    CN-2: блоки «HTML-код» сохраняет только суперадмин.
    """
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    content = load_content()
    page = get_page_by_slug(content, slug)
    if not page:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    expected = payload.get('expected_updated_at')
    current_updated = page.get('updated_at', '')
    if expected and current_updated and expected != current_updated:
        return jsonify({'success': False,
                        'error': 'Страница изменена другим админом',
                        'updated_at': current_updated}), 409
    blocks = payload.get('blocks')
    if not isinstance(blocks, list):
        return jsonify({'success': False, 'error': 'blocks обязателен'}), 400
    blocks = [b for b in blocks if isinstance(b, dict)]
    if _admin_role() != 'superadmin':
        blocks = [b for b in blocks if b.get('type') != 'html']
    meta = payload.get('page') or {}
    for k in ('title', 'h1', 'meta_description', 'meta_keywords', 'canonical'):
        if k in meta:
            page[k] = meta[k]
    page['blocks'] = blocks
    page['content_html'] = blocks_module.render_blocks(blocks)
    page['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    save_content(content)
    invalidate_site_cache()
    # Вернуть значение из хранилища: формат после сохранения — канонический
    saved = get_page_by_slug(load_content(), slug) or page
    return jsonify({'success': True, 'updated_at': saved.get('updated_at', '')})


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
    for k in ('title', 'h1', 'meta_description', 'meta_keywords', 'canonical', 'url'):
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


# Валидация загрузки файлов в админку (Этап 6.1, 2.2.7 ТЗ): whitelist
# расширений, лимит 10 МБ, проверка MIME/содержимого, уникальные имена.
UPLOAD_ALLOWED_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
                       'css', 'js', 'txt', 'pdf', 'woff', 'woff2'}
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
# Ожидаемый тип содержимого по расширению (префикс content_type файла).
UPLOAD_CONTENT_PREFIX = {
    'png': 'image/', 'jpg': 'image/', 'jpeg': 'image/', 'gif': 'image/',
    'webp': 'image/', 'svg': 'image/',
    'css': 'text/css', 'js': ('text/javascript', 'application/javascript'),
    'txt': 'text/plain', 'pdf': 'application/pdf',
    'woff': ('font/', 'application/'), 'woff2': ('font/', 'application/'),
}
# Магические байты растровых изображений (проверяются, если Pillow недоступен).
_IMAGE_MAGIC = {
    'png': (b'\x89PNG\r\n\x1a\n',),
    'jpg': (b'\xff\xd8\xff',),
    'gif': (b'GIF87a', b'GIF89a'),
    'webp': (b'RIFF',),   # точное «WEBP» в начале чанка проверяет PIL
}
try:
    from PIL import Image as _PILImage
except Exception:
    _PILImage = None


def _upload_content_ok(ext, content_type, data):
    """MIME-проверка file.content_type + сигнатура изображения (PIL опционально)."""
    expected = UPLOAD_CONTENT_PREFIX.get(ext)
    if expected:
        prefixes = expected if isinstance(expected, tuple) else (expected,)
        ctype = (content_type or '').split(';')[0].strip().lower()
        if not ctype or not any(ctype.startswith(p) for p in prefixes):
            return False
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        if _PILImage is not None:
            try:
                import io
                img = _PILImage.open(io.BytesIO(data))
                img.verify()
                return True
            except Exception:
                return False
        return any(data.startswith(sig) for sig in _IMAGE_MAGIC.get(ext, ()))
    return True


@app.route('/admin/api/upload', methods=['POST'])
@admin_required
def admin_upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename'}), 400
    # Имя файла из запроса — только источник расширения; путь/имя не доверяем.
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in UPLOAD_ALLOWED_EXTS:
        return jsonify({'success': False,
                        'error': f'Тип файла .{ext or "?"} не разрешён. '
                                 f'Разрешены: {", ".join(sorted(UPLOAD_ALLOWED_EXTS))}'}), 400
    data = file.read()
    if not data:
        return jsonify({'success': False, 'error': 'Пустой файл'}), 400
    if len(data) > UPLOAD_MAX_BYTES:
        return jsonify({'success': False,
                        'error': 'Файл больше 10 МБ'}), 400
    if not _upload_content_ok(ext, file.content_type, data):
        return jsonify({'success': False,
                        'error': 'Содержимое файла не соответствует расширению/MIME'}), 400
    upload_dir = BASE_DIR / 'static' / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Уникальное имя + запрет перезаписи: коллизия (или повтор) — ошибка, а не затирание.
    for _ in range(5):
        filename = f"{secrets.token_hex(8)}.{ext}"
        path = upload_dir / filename
        if not path.exists():
            break
    else:
        return jsonify({'success': False, 'error': 'Не удалось сгенерировать уникальное имя'}), 500
    path.write_bytes(data)
    return jsonify({'success': True, 'url': f'/static/uploads/{filename}'})


BASE_PAGE_TEMPLATE = 'pages/_new_page_base.html'


def _create_page(content, slug, title, url, blocks):
    """Создать страницу: копия BASE_PAGE_TEMPLATE + запись в pages + рендер HTML.

    Общий helper для add-page (CN-1) и создания страницы из пресета (CN-T9.2).
    Выбрасывает ValueError с сообщением об ошибке (вызывающий маппит в 400).
    """
    slug = str(slug or '').strip()
    url = str(url or '').strip() or f'/{slug}/'
    if not slug:
        raise ValueError('slug и url обязательны')
    if not re.match(r'^[a-z0-9_\-]+$', slug):
        raise ValueError('slug может содержать только латинские буквы, цифры, - и _')
    if any(p['slug'] == slug for p in content['pages']):
        raise ValueError('Страница с таким slug уже существует')
    if any(p['url'] == url for p in content['pages']):
        raise ValueError('Страница с таким URL уже существует')

    template_name = f'pages/{slug}.html'
    dest = BASE_DIR / 'templates' / template_name
    base = BASE_DIR / 'templates' / BASE_PAGE_TEMPLATE
    if base.exists():
        shutil.copy(base, dest)
    else:
        # Minimal fallback template
        dest.write_text("""<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"UTF-8\">\n<title>{{ page.title }}</title>\n<meta name=\"description\" content=\"{{ page.meta_description }}\">\n<link rel=\"stylesheet\" href=\"/theme.css?v={{ site.theme.version | default(1) }}\">\n</head>\n<body>\n{{ page.content_html | safe }}\n</body>\n</html>\n""", encoding='utf-8')

    page = {
        'slug': slug,
        'url': url,
        'title': title or slug,
        'h1': title or slug,
        'meta_description': '',
        'template': template_name,
        'blocks': blocks if isinstance(blocks, list) else [],
    }
    page['content_html'] = blocks_module.render_blocks(page['blocks'])
    content['pages'].append(page)
    save_content(content)
    invalidate_site_cache()
    return page


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
    content = load_content()
    try:
        page = _create_page(content, slug, title, url, [
            {'type': 'heading', 'level': 'h1', 'text': title or slug},
            {'type': 'text', 'text': 'Опишите здесь, чем полезна эта страница. Текст можно форматировать абзацами.'},
        ])
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'page': page})


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


@app.route('/admin/api/duplicate-page', methods=['POST'])
@admin_required
def admin_duplicate_page():
    """Дублирование страницы (CN-1): копия с slug `<slug>-copy`, `-copy2`…."""
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    if not slug:
        return jsonify({'success': False, 'error': 'slug обязателен'}), 400
    content = load_content()
    page = next((p for p in content['pages'] if p['slug'] == slug), None)
    if not page:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    new_slug = slug + '-copy'
    n = 2
    existing = {p['slug'] for p in content['pages']}
    while new_slug in existing:
        new_slug = f'{slug}-copy{n}'
        n += 1
    copy = dict(page)
    copy['slug'] = new_slug
    copy['url'] = f'/{new_slug}/'
    copy['title'] = (page.get('title') or slug) + ' (копия)'
    copy['h1'] = (page.get('h1') or page.get('title') or slug) + ' (копия)'
    copy['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    if isinstance(copy.get('blocks'), list):
        copy['blocks'] = [dict(b) for b in copy['blocks'] if isinstance(b, dict)]
        copy['content_html'] = blocks_module.render_blocks(copy['blocks'])
    content['pages'].append(copy)
    save_content(content)
    invalidate_site_cache()
    return jsonify({'success': True, 'slug': new_slug})


# ---------- Цикл 2 конструктора (CN-T5/CN-T6/CN-T7/CN-T9/CN-T10/CN-T12) ----------

_VERSION_META_FIELDS = ('title', 'h1', 'meta_description', 'meta_keywords', 'canonical', 'url')
_PAGE_META_FIELDS = ('title', 'h1', 'meta_description', 'meta_keywords', 'canonical')
_VERSIONS_PER_PAGE = 10


def _page_draft_blocks(page):
    """Блоки черновика страницы (draft_blocks), если черновика нет — опубликованные/текущие."""
    draft = page.draft_blocks if isinstance(page.draft_blocks, dict) else None
    if isinstance(draft, dict) and isinstance(draft.get('blocks'), list):
        return draft['blocks']
    return page.published_blocks if isinstance(page.published_blocks, list) else \
        (page.blocks if isinstance(page.blocks, list) else [])


def _save_page_version(page, blocks, label=''):
    """Снапшот версии страницы (CN-T10.2): блоки + мета-поля; максимум 10 на страницу."""
    from models import PageVersion
    fields = {k: (getattr(page, k, '') or '') for k in _VERSION_META_FIELDS}
    db.session.add(PageVersion(
        page_slug=page.slug,
        blocks=copy.deepcopy(blocks),
        page_fields=fields,
        label=str(label or '')[:120],
        created_by=session.get('admin_user') or '',
    ))
    versions = (db.session.query(PageVersion)
                .filter_by(page_slug=page.slug)
                .order_by(PageVersion.id.desc()).all())
    for old in versions[_VERSIONS_PER_PAGE:]:
        db.session.delete(old)


@app.route('/admin/api/page-publish', methods=['POST'])
@admin_required
def admin_page_publish():
    """Публикация страницы (CN-T10.3): draft_blocks (или blocks, если черновика нет)
    → published_blocks, пересборка content_html для посетителей, автоснапшот «Авто»."""
    from models import Page
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    if not slug:
        return jsonify({'success': False, 'error': 'slug обязателен'}), 400
    page = db.session.query(Page).filter_by(slug=slug).first()
    if page is None:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    blocks = _page_draft_blocks(page)
    page.published_blocks = copy.deepcopy(blocks)
    page.content_html = blocks_module.render_blocks(blocks)
    page.updated_at = datetime.now(timezone.utc)
    _save_page_version(page, blocks, label='Авто')
    db.session.commit()
    invalidate_site_cache()
    return jsonify({'success': True,
                    'updated_at': page.updated_at.isoformat(timespec='seconds')})


@app.route('/admin/api/page-version-save', methods=['POST'])
@admin_required
def admin_page_version_save():
    """Сохранить версию страницы (CN-T10.2): снапшот draft_blocks||blocks + мета-полей."""
    from models import Page
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    label = str(payload.get('label') or '').strip()[:120]
    if not slug:
        return jsonify({'success': False, 'error': 'slug обязателен'}), 400
    page = db.session.query(Page).filter_by(slug=slug).first()
    if page is None:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    blocks = _page_draft_blocks(page)
    _save_page_version(page, blocks, label=label)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/api/page-versions')
@admin_required
def admin_page_versions():
    """Список версий страницы (CN-T10.2): {id, label, created_at, created_by, blocks_count}."""
    from models import PageVersion
    slug = request.args.get('slug', '').strip()
    if not slug:
        return jsonify({'success': False, 'error': 'slug обязателен'}), 400
    rows = (db.session.query(PageVersion)
            .filter_by(page_slug=slug)
            .order_by(PageVersion.id.desc()).all())
    return jsonify({'success': True, 'versions': [{
        'id': r.id,
        'label': r.label or '',
        'created_at': r.created_at.isoformat(timespec='seconds') if r.created_at else '',
        'created_by': r.created_by or '',
        'blocks_count': len(r.blocks) if isinstance(r.blocks, list) else 0,
    } for r in rows]})


@app.route('/admin/api/page-version-restore', methods=['POST'])
@admin_required
def admin_page_version_restore():
    """Откат к версии (CN-T10.2): blocks+мета версии → draft_blocks (БЕЗ публикации)."""
    from models import Page, PageVersion
    payload = request.get_json() or {}
    slug = payload.get('slug', '').strip()
    version_id = payload.get('version_id')
    if not slug or not version_id:
        return jsonify({'success': False, 'error': 'slug и version_id обязательны'}), 400
    page = db.session.query(Page).filter_by(slug=slug).first()
    if page is None:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'version_id должен быть числом'}), 400
    version = db.session.get(PageVersion, version_id)
    if version is None or version.page_slug != slug:
        return jsonify({'success': False, 'error': 'Версия не найдена'}), 404
    blocks = version.blocks if isinstance(version.blocks, list) else []
    page.draft_blocks = {'blocks': copy.deepcopy(blocks),
                         'saved_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    fields = version.page_fields if isinstance(version.page_fields, dict) else {}
    for k in _VERSION_META_FIELDS:
        if k in fields:
            setattr(page, k, fields.get(k) or '')
    db.session.commit()
    invalidate_site_cache()
    return jsonify({'success': True})


@app.route('/admin/api/block-templates', methods=['GET', 'POST', 'DELETE'])
@admin_required
def admin_block_templates():
    """«Мои блоки» (CN-T9.1): список / создание / удаление сохранённых блоков."""
    from models import BlockTemplate
    if request.method == 'GET':
        rows = db.session.query(BlockTemplate).order_by(BlockTemplate.id).all()
        return jsonify({'success': True, 'templates': [{
            'id': r.id,
            'name': r.name or '',
            'category': r.category or '',
            'block': r.block,
            'created_at': r.created_at.isoformat(timespec='seconds') if r.created_at else '',
        } for r in rows]})
    if request.method == 'POST':
        payload = request.get_json() or {}
        name = str(payload.get('name') or '').strip()[:120]
        category = str(payload.get('category') or '').strip()[:60]
        block = payload.get('block')
        if not name:
            return jsonify({'success': False, 'error': 'name обязателен'}), 400
        if not isinstance(block, dict):
            return jsonify({'success': False, 'error': 'block должен быть объектом'}), 400
        row = BlockTemplate(name=name, category=category, block=copy.deepcopy(block))
        db.session.add(row)
        db.session.commit()
        return jsonify({'success': True, 'id': row.id})
    payload = request.get_json() or {}
    row_id = payload.get('id')
    try:
        row_id = int(row_id)
    except (TypeError, ValueError):
        row_id = None
    row = db.session.get(BlockTemplate, row_id) if row_id else None
    if row is None:
        return jsonify({'success': False, 'error': 'Шаблон блока не найден'}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/api/page-templates')
@admin_required
def admin_page_templates():
    """Шаблоны страниц (CN-T9.2): курируемые пресеты блоков."""
    return jsonify({'success': True, 'templates': page_templates_module.list_templates()})


@app.route('/admin/api/page-templates/create', methods=['POST'])
@admin_required
def admin_page_templates_create():
    """Создать страницу из пресета (CN-T9.2): {template_id, slug, title, url?}."""
    payload = request.get_json() or {}
    preset = page_templates_module.get_template(payload.get('template_id'))
    if preset is None:
        return jsonify({'success': False, 'error': 'Шаблон не найден'}), 404
    slug = str(payload.get('slug') or '').strip()
    if not slug:
        return jsonify({'success': False, 'error': 'slug обязателен'}), 400
    title = str(payload.get('title') or '').strip()
    url = str(payload.get('url') or '').strip() or f'/{slug}/'
    content = load_content()
    try:
        page = _create_page(content, slug, title or preset['name'], url,
                            copy.deepcopy(preset['blocks']))
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'page': page})


@app.route('/admin/api/global-zones', methods=['GET', 'POST'])
@admin_required
def admin_global_zones():
    """Глобальные зоны шапка/подвал (CN-T6): GET — текущие (дефолт создаётся
    на лету без сохранения), POST — валидация, merge и сохранение."""
    if request.method == 'GET':
        zones_cfg = zones_module.get_zones()
        return jsonify({'success': True, **zones_cfg})
    payload = request.get_json() or {}
    current = zones_module.get_zones()
    if 'header' in payload:
        current['header'] = zones_module.validate_zone('header', payload.get('header'))
    if 'footer' in payload:
        current['footer'] = zones_module.validate_zone('footer', payload.get('footer'))
    storage.set_setting('global_zones', current)
    invalidate_site_cache()
    return jsonify({'success': True, **current})


PAGE_SETTINGS_DEFAULTS = {
    'og_title': '',
    'og_description': '',
    'og_image': '',
    'favicon': '',
    'page_bg': '',
    'page_link_color': '',
    'custom_css': '',
    'custom_js': '',
    'override_header': False,
    'override_footer': False,
    'header_config': None,
    'footer_config': None,
}
_PAGE_SETTINGS_URL_FIELDS = ('og_image', 'favicon')
_PAGE_SETTINGS_COLOR_FIELDS = ('page_bg', 'page_link_color')


def _page_settings_url_ok(value):
    """og_image/favicon: http(s) или относительный путь; иначе False."""
    v = str(value or '').strip()
    if not v:
        return True
    low = v.lower()
    if low.startswith(('javascript:', 'data:', 'vbscript:')):
        return False
    return low.startswith(('http://', 'https://', '/'))


@app.route('/admin/api/page-settings', methods=['GET', 'POST'])
@admin_required
def admin_page_settings():
    """Настройки страницы (CN-T5, сервер): мета-поля в колонки Page, остальное
    в Page.settings JSON. custom_css/custom_js — только суперадмин."""
    from models import Page
    payload = request.get_json(silent=True) or {}
    slug = request.args.get('slug', '') if request.method == 'GET' else payload.get('slug', '')
    slug = str(slug or '').strip()
    page = db.session.query(Page).filter_by(slug=slug).first() if slug else None
    if page is None:
        return jsonify({'success': False, 'error': 'Страница не найдена'}), 404

    current = dict(PAGE_SETTINGS_DEFAULTS)
    if isinstance(page.settings, dict):
        current.update({k: v for k, v in page.settings.items() if k in PAGE_SETTINGS_DEFAULTS})

    if request.method == 'GET':
        return jsonify({
            'success': True,
            'slug': slug,
            'settings': current,
            'meta': {k: getattr(page, k) or '' for k in _PAGE_META_FIELDS},
        })

    # POST: мета-поля → колонки страницы
    meta = payload.get('meta') or {}
    if isinstance(meta, dict):
        for k in _PAGE_META_FIELDS:
            if k in meta:
                setattr(page, k, str(meta.get(k) or '')[:500])
    # URL-поля настроек: невалидные отклоняем целиком
    for k in _PAGE_SETTINGS_URL_FIELDS:
        if k in payload and not _page_settings_url_ok(payload.get(k)):
            return jsonify({'success': False, 'error': f'Некорректный URL в поле {k}'}), 400
    # custom_css/custom_js — только суперадмин
    wants_code = any(str(payload.get(k) or '').strip()
                     for k in ('custom_css', 'custom_js') if k in payload)
    if wants_code and _admin_role() != 'superadmin':
        return jsonify({'success': False,
                        'error': 'custom_css/custom_js доступны только суперадмину'}), 403
    for k in ('og_title', 'og_description'):
        if k in payload:
            current[k] = str(payload.get(k) or '')[:500]
    for k in _PAGE_SETTINGS_URL_FIELDS:
        if k in payload:
            current[k] = str(payload.get(k) or '').strip()[:500]
    for k in ('custom_css', 'custom_js'):
        if k in payload:
            current[k] = str(payload.get(k) or '')[:20000]
    for k in _PAGE_SETTINGS_COLOR_FIELDS:
        if k in payload:
            v = str(payload.get(k) or '').strip()
            current[k] = v if re.fullmatch(r'#[0-9a-fA-F]{3,8}', v) else current.get(k, '')
    for k in ('override_header', 'override_footer'):
        if k in payload:
            current[k] = bool(payload.get(k))
    for k, kind in (('header_config', 'header'), ('footer_config', 'footer')):
        if k not in payload:
            continue
        if payload.get(k) is None:
            current[k] = None
        elif isinstance(payload.get(k), dict):
            current[k] = zones_module.validate_zone(kind, payload.get(k))
        else:
            return jsonify({'success': False, 'error': f'{k} должен быть объектом'}), 400
    page.settings = current
    db.session.commit()
    invalidate_site_cache()
    return jsonify({'success': True, 'slug': slug, 'settings': current})


THEME_HISTORY_LIMIT = 10


def _theme_history():
    """История темы (CN-T7.3): список {theme, saved_at, saved_by} из site_settings."""
    history = storage.get_setting('theme_history')
    if not isinstance(history, list):
        return []
    return [h for h in history if isinstance(h, dict)]


def _push_theme_history(theme):
    """Перед сохранением/откатом темы — пушить ТЕКУЩЕЕ состояние в историю (макс. 10)."""
    history = _theme_history()
    history.insert(0, {
        'theme': copy.deepcopy(theme),
        'saved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'saved_by': session.get('admin_user') or '',
    })
    storage.set_setting('theme_history', history[:THEME_HISTORY_LIMIT])


@app.route('/admin/api/theme', methods=['GET', 'POST'])
@admin_required
def admin_theme():
    """Текущая тема (GET) и сохранение/откат с историей (POST) — CN-T7.1/CN-T7.3.

    POST {theme: {...}} — валидация через _validated_theme, merge с текущей,
    бамп version, пуш текущего состояния в историю.
    POST {action: 'restore_history', index: N} — откат к записи истории
    (текущее состояние тоже пушится в историю перед откатом).
    """
    content = load_content()
    site = content.setdefault('site', {})
    if request.method == 'GET':
        return jsonify({'success': True,
                        'theme': site.get('theme') or dict(THEME_DEFAULTS),
                        'history': _theme_history()})
    payload = request.get_json() or {}
    current_theme = site.get('theme') or dict(THEME_DEFAULTS)

    if payload.get('action') == 'restore_history':
        try:
            index = int(payload.get('index'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'index обязателен'}), 400
        history = _theme_history()
        if not 0 <= index < len(history):
            return jsonify({'success': False, 'error': 'Запись истории не найдена'}), 404
        _push_theme_history(current_theme)
        restored = _validated_theme(history[index].get('theme')) or dict(THEME_DEFAULTS)
        restored['version'] = int(current_theme.get('version') or 1) + 1
        site['theme'] = restored
        save_content(content)
        invalidate_site_cache()
        return jsonify({'success': True, 'theme': restored, 'history': _theme_history()})

    validated = _validated_theme(payload.get('theme'))
    if validated is None:
        return jsonify({'success': False, 'error': 'Некорректная тема'}), 400
    _push_theme_history(current_theme)
    theme = dict(current_theme)
    theme.update(validated)
    theme['version'] = int(current_theme.get('version') or 1) + 1
    site['theme'] = theme
    save_content(content)
    invalidate_site_cache()
    return jsonify({'success': True, 'theme': theme, 'history': _theme_history()})


@app.route('/admin/api/fonts')
@admin_required
def admin_fonts():
    """Курируемый список Google-шрифтов (CN-T7.2); ?q= — фильтр по family."""
    return jsonify({'success': True, 'fonts': fonts_module.list_fonts(request.args.get('q'))})


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

    # SEO title/description из шаблонов с переменными {name} {brand} {price}… (SEO-2)
    seo = content.get('site', {}).get('seo', {})
    page = dict(get_page_by_slug(content, 'catalog__namatrasniki'))
    page['title'] = _render_meta_template(seo.get('product_title_template'), product, content.get('site', {})) or page.get('title', '')
    page['meta_description'] = _render_meta_template(seo.get('product_description_template'), product, content.get('site', {})) or page.get('meta_description', '')

    ctx = make_context(content, page)
    ctx['seo_product'] = _seo_product(product, request.host_url)
    ctx['seo_og_image'] = _absolute_url(product.get('photo'), request.host_url) or ctx['seo_og_image']
    # Крошки и canonical карточки: Главная → Каталог → товар (SEO-1/SEO-4)
    product_url = request.host_url.rstrip('/') + request.path
    ctx['seo_canonical'] = product_url
    ctx['seo_breadcrumbs'] = [
        {'name': 'Главная', 'url': request.host_url},
        {'name': 'Каталог', 'url': request.host_url.rstrip('/') + '/catalog/'},
        {'name': product.get('name', ''), 'url': product_url},
    ]
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
@_rate_limit(20)
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
@_rate_limit(20)
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
@_rate_limit(20)
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


@app.route('/admin/api/dashboard')
@admin_required
def admin_api_dashboard():
    """Дашборд (AD-3): заявки, новые клиенты, ошибки синка, популярные товары."""
    from models import SyncLog
    now = datetime.now(timezone.utc)
    orders_all = db.session.query(Order).all()
    orders_new = [o for o in orders_all if o.status == 'new']
    customers_all = db.session.query(Customer).all()
    new_customers = [c for c in customers_all
                     if c.created_at and (now - _aware(c.created_at)).days <= 7]
    sync_errors = [e.to_dict() for e in
                   db.session.query(SyncLog).filter_by(status='error')
                   .order_by(SyncLog.id.desc()).limit(10).all()]
    popular = customers.get_popular_products(get_products(), limit=5)
    return jsonify({
        'success': True,
        'orders_total': len(orders_all),
        'orders_new': len(orders_new),
        'orders_today': len([o for o in orders_all
                             if o.created_at and (now - _aware(o.created_at)).days < 1]),
        'customers_total': len(customers_all),
        'customers_new_7d': len(new_customers),
        'sync_errors': sync_errors,
        'popular_products': [{'nm_id': p['product'].get('nm_id') or p['product'].get('id'),
                              'name': p['product'].get('name'),
                              'orders': p.get('orders', 0), 'likes': p.get('likes', 0),
                              'score': p.get('score', 0)} for p in popular],
    })


@app.route('/admin/api/audit')
@admin_required
def admin_api_audit():
    """Аудит-лог действий админов (AD-4)."""
    from models import AuditLog
    limit = request.args.get('limit', type=int) or 200
    limit = max(1, min(1000, limit))
    rows = (db.session.query(AuditLog)
            .order_by(AuditLog.id.desc()).limit(limit).all())
    return jsonify({'success': True, 'entries': [r.to_dict() for r in rows]})


@app.route('/admin/api/admin-users', methods=['GET', 'POST'])
@admin_required
def admin_api_admin_users():
    """Список админов / создание нового (только суперадмин, AD-2)."""
    from models import AdminUser
    if _admin_role() != 'superadmin':
        return jsonify({'success': False, 'error': 'Только суперадмин'}), 403
    if request.method == 'GET':
        rows = db.session.query(AdminUser).order_by(AdminUser.id).all()
        return jsonify({'success': True, 'users': [{
            'id': r.id, 'username': r.username, 'role': r.role or 'superadmin',
            'created_at': r.created_at.isoformat() if r.created_at else '',
        } for r in rows]})
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', '').strip()
    if not username or not password or role not in ('superadmin', 'content', 'orders'):
        return jsonify({'success': False, 'error': 'Нужны username, password и роль (superadmin|content|orders)'}), 400
    if db.session.query(AdminUser).filter_by(username=username).first():
        return jsonify({'success': False, 'error': 'Такой логин уже есть'}), 400
    db.session.add(AdminUser(username=username,
                             password_hash=generate_password_hash(password), role=role,
                             must_change_password=True))
    db.session.commit()
    _audit_log('admin_user.create', 'admin_user', username, {'role': role})
    return jsonify({'success': True})


@app.route('/admin/api/admin-user/delete', methods=['POST'])
@admin_required
def admin_api_admin_user_delete():
    """Удаление админа (суперадмин; себя удалить нельзя, AD-2)."""
    from models import AdminUser
    if _admin_role() != 'superadmin':
        return jsonify({'success': False, 'error': 'Только суперадмин'}), 403
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    if not username or username == session.get('admin_user'):
        return jsonify({'success': False, 'error': 'Нельзя удалить себя'}), 400
    row = db.session.query(AdminUser).filter_by(username=username).first()
    if row is None:
        return jsonify({'success': False, 'error': 'Пользователь не найден'}), 404
    db.session.delete(row)
    db.session.commit()
    _audit_log('admin_user.delete', 'admin_user', username)
    return jsonify({'success': True})


def _aware(dt):
    """Наивный datetime (SQLite) → aware UTC для арифметики."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _sqlite_db_path():
    """Путь к файлу SQLite БД из DATABASE_URL (None если не sqlite)."""
    uri = app.config.get('SQLALCHEMY_DATABASE_URI') or os.environ.get('DATABASE_URL', '')
    if uri.startswith('sqlite:///'):
        return Path(uri[len('sqlite:///'):])
    return None


@app.route('/admin/api/backup/full')
@admin_required
def admin_api_backup_full():
    """Полный бэкап (AD-5): zip с файлом БД и полным контентом. Суперадмин."""
    if _admin_role() != 'superadmin':
        return jsonify({'success': False, 'error': 'Только суперадмин'}), 403
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        db_file = _sqlite_db_path()
        if db_file and db_file.exists():
            zf.write(db_file, 'ekotoriya.db')
        content = load_content()
        zf.writestr('content.json', json.dumps(content, ensure_ascii=False, indent=2))
    _audit_log('backup.create', 'backup', '', {})
    return Response(buf.getvalue(), mimetype='application/zip', headers={
        'Content-Disposition': 'attachment; filename="ekotoriya-backup.zip"'})


@app.route('/admin/api/restore', methods=['POST'])
@admin_required
def admin_api_restore():
    """Восстановление из бэкапа (AD-5): zip с content.json и/или ekotoriya.db."""
    if _admin_role() != 'superadmin':
        return jsonify({'success': False, 'error': 'Только суперадмин'}), 403
    import zipfile
    upload = request.files.get('file')
    if not upload or not upload.filename.endswith('.zip'):
        return jsonify({'success': False, 'error': 'Нужен .zip файл бэкапа'}), 400
    try:
        zf = zipfile.ZipFile(upload.stream)
        names = set(zf.namelist())
        restored = []
        if 'content.json' in names:
            content = json.loads(zf.read('content.json').decode('utf-8'))
            save_content(content)
            restored.append('content.json')
        if 'ekotoriya.db' in names:
            db_file = _sqlite_db_path()
            if db_file is None:
                return jsonify({'success': False, 'error': 'Восстановление БД доступно только для SQLite'}), 400
            backup_current = db_file.with_suffix('.db.before-restore')
            if db_file.exists():
                shutil.copy2(db_file, backup_current)
            db_file.write_bytes(zf.read('ekotoriya.db'))
            db.engine.dispose()  # переподключение к новому файлу БД
            restored.append('ekotoriya.db')
    except Exception as e:
        return jsonify({'success': False, 'error': f'Ошибка восстановления: {e}'}), 400
    if not restored:
        return jsonify({'success': False, 'error': 'В архиве нет content.json или ekotoriya.db'}), 400
    invalidate_site_cache()
    _audit_log('backup.restore', 'backup', '', {'restored': restored})
    return jsonify({'success': True, 'restored': restored})


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


# Разрешённые хосты изображений маркетплейсов (Этап 6.1): точный список
# шаблонов вместо широких подстрок (защита от SSRF-обхода вида evilbasket-.ru).
PROXY_HOST_PATTERNS = [
    re.compile(r'^basket-\d{2}\.wbbasket\.ru$'),
    re.compile(r'^static-basket-\d{2}\.wb\.ru$'),
    re.compile(r'^cdn\d*\.wildberries\.ru$'),
    re.compile(r'^www\.wildberries\.ru$'),
    re.compile(r'^cdn\d*\.ozone\.ru$'),
    re.compile(r'^cdn\d*\.ozon\.ru$'),
]
_PROXY_BLOCKED_HOSTNAMES = {'localhost', '127.0.0.1', '::1', '0.0.0.0'}


def _proxy_host_allowed(host):
    """True только для разрешённых CDN-хостов маркетплейсов; IP и localhost — запрещены."""
    host = (host or '').strip().lower().split(':')[0]
    if not host or host in _PROXY_BLOCKED_HOSTNAMES:
        return False
    try:
        ipaddress.ip_address(host)   # любой IP-литерал (в т.ч. внутренние 10/172.16/192.168)
        return False
    except ValueError:
        pass
    return any(p.fullmatch(host) for p in PROXY_HOST_PATTERNS)


def _convert_to_webp(data, max_side=1600):
    """Конвертация растрового изображения в WebP с ресайзом до max_side px.

    Pillow опционально: без неё возвращает None и прокси отдаёт оригинал
    (Этап 6.2). SVG и нерастровые форматы не трогаем.
    """
    if _PILImage is None:
        return None
    try:
        import io
        img = _PILImage.open(io.BytesIO(data))
        img.load()
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), _PILImage.LANCZOS)
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'WEBP', quality=82)
        return buf.getvalue()
    except Exception:
        return None


@app.route('/proxy/image')
def proxy_image():
    """Proxy and cache external product images to avoid ORB/CORS issues.

    If the upstream image is unreachable, a local placeholder is returned so
    the layout does not break.

    Этап 6.1: только точный список CDN-хостов WB/Ozon, file:// и внутренние
    IP запрещены (SSRF). Этап 6.2: новые картинки кэшируются как WebP ≤1600px.
    """
    url = request.args.get('url', '').strip()
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return send_from_directory(BASE_DIR / 'static', 'noimage.png')
    if not _proxy_host_allowed(parsed.hostname):
        return send_from_directory(BASE_DIR / 'static', 'noimage.png')
    cache_key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:32]
    ext = Path(parsed.path).suffix or '.jpg'
    if ext.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.bmp', '.ico'):
        ext = '.jpg'
    webp_file = IMAGE_CACHE_DIR / f'{cache_key}.webp'
    cache_file = IMAGE_CACHE_DIR / f'{cache_key}{ext}'

    def _serve(path, mimetype):
        return send_from_directory(IMAGE_CACHE_DIR, path.name,
                                   mimetype=mimetype, max_age=86400 * 30)

    if webp_file.exists():
        return _serve(webp_file, 'image/webp')
    if cache_file.exists():   # старый кэш, записанный до WebP-конвертации
        content_type, _ = mimetypes.guess_type(str(cache_file))
        return _serve(cache_file, content_type or 'image/jpeg')

    try:
        r = requests.get(url, timeout=6, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        })
        if r.status_code == 200 and r.content:
            if ext.lower() != '.svg':
                converted = _convert_to_webp(r.content)
                if converted:
                    webp_file.write_bytes(converted)
                    return _serve(webp_file, 'image/webp')
            cache_file.write_bytes(r.content)
    except Exception:
        pass
    if cache_file.exists():
        content_type, _ = mimetypes.guess_type(str(cache_file))
        return _serve(cache_file, content_type or 'image/jpeg')
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

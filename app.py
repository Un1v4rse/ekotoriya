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


@app.template_filter('digits')
def _digits_filter(s):
    """Только цифры из строки — для tel:-ссылок из настроек сайта."""
    import re
    return re.sub(r'\D', '', str(s or ''))

# Дефолтные контакты клонированного шаблона. В content_html-страницах
# (снапшоты перенесённого дизайна) они захардкожены — подменяем при рендере
# на актуальные значения из настроек сайта, чтобы любое изменение в админке
# мгновенно отражалось на всех страницах.
_DEFAULT_PHONE = '8 800 201 08 24'
_DEFAULT_EMAIL = 'helloekotoriya@yandex.ru'
_DEFAULT_ADDRESS = 'г. Череповец, ул. Гоголя 56'


def _sync_contacts_html(html, site):
    """Заменить дефолтные контакты в HTML-строке на текущие из настроек."""
    if not html or not isinstance(html, str):
        return html
    phone = (site or {}).get('phone') or _DEFAULT_PHONE
    email = (site or {}).get('email') or _DEFAULT_EMAIL
    address = (site or {}).get('address') or _DEFAULT_ADDRESS
    digits = re.sub(r'\D', '', phone)
    out = html.replace('tel:88002010824', 'tel:' + digits)
    out = re.sub(r'mailto:helloekotoriya@yandex\.ru', 'mailto:' + email, out,
                 flags=re.I)
    out = out.replace(_DEFAULT_PHONE, phone)
    out = re.sub(r'helloekotoriya@yandex\.ru', email, out, flags=re.I)
    out = out.replace(_DEFAULT_ADDRESS, address)
    # PG-3.1: битый якорь email из клона (вложенный mailto с экранированным
    # HTML внутри атрибута) — заменяем на чистый, с актуальным адресом.
    out = re.sub(
        r"<a href='mailto:[^']*'>\s*<a href=\"mailto:[^\"]*\">[^<]*</a>\s*</a>",
        '<a href="mailto:' + email + '">' + email + '</a>', out)
    # PG-3.2: Яндекс.Карта под блоком информации. Маркер wb-contacts-map —
    # идемпотентность: повторные рендеры не плодят блоки. Адрес для поиска —
    # из настройки site.map_query (можно сменить в админке).
    if 'wb-contacts-map' not in out:
        import urllib.parse as _up
        query = str((site or {}).get('map_query') or '').strip() \
            or 'Череповец, улица Гоголя, 56'
        embed = 'https://yandex.ru/map-widget/v1/?text=' \
            + _up.quote(query) + '&z=16'
        map_html = (
            '<div class="maxwidth-theme" style="margin:2rem auto 0;">'
            '<div class="wb-contacts-map" '
            'style="position:relative;padding-top:56.25%;border-radius:8px;'
            'overflow:hidden;">'
            '<iframe src="' + embed + '" loading="lazy" '
            'style="position:absolute;top:0;left:0;width:100%;height:100%;'
            'border:0;" allowfullscreen></iframe></div></div>')
        out = out + map_html
    return out
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

# Миграция «цикл 3»: products.site_section — раздел сайта (управление из админки).
try:
    import migrate_cycle3
    migrate_cycle3.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle3] пропущено: {_migrate_err}')

# Миграция «цикл 4»: events.payload — снапшот товара в лайках (избранное).
try:
    import migrate_cycle4
    migrate_cycle4.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle4] пропущено: {_migrate_err}')

# Миграция «цикл 5»: products.manual_overrides — поля, закреплённые от перезаписи синком.
try:
    import migrate_cycle5
    migrate_cycle5.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle5] пропущено: {_migrate_err}')

# Миграция «цикл 6»: subscribers — подписка на рассылку из подвала сайта.
try:
    import migrate_cycle6
    migrate_cycle6.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle6] пропущено: {_migrate_err}')

# Миграция «цикл 7» (цикл 4 / этап 5): feedback_requests — заявки с кнопок конструктора.
try:
    import migrate_cycle7
    migrate_cycle7.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle7] пропущено: {_migrate_err}')

# Миграция «цикл 8» (цикл 4 / этап 6): reviews — отзывы покупателей (модерация).
try:
    import migrate_cycle8
    migrate_cycle8.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle8] пропущено: {_migrate_err}')

# Миграция «цикл 9» (цикл 4 / этап 7): чат клиент↔магазин (chat_dialogs, chat_messages).
try:
    import migrate_cycle9
    migrate_cycle9.migrate(app)
except Exception as _migrate_err:
    print(f'[migrate_cycle9] пропущено: {_migrate_err}')

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
    frame-src — 'self' (редактор страниц грузит контент в same-origin iframe)
    плюс Яндекс.Карты и YouTube/Vimeo (блок map/video).
    """
    path = request.path
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' https:; "
        "font-src 'self' data:; "
        "connect-src 'self' https:; "
        "frame-src 'self' https://yandex.ru https://yastatic.net https://www.youtube.com https://player.vimeo.com; "
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
    elif path == '/theme.css':
        # Генерируемый CSS (тема + BLOCKS_CSS). Генерация дешёвая, зато правки
        # стилей блоков/меню доезжают до браузеров без бампа версии темы.
        response.headers['Cache-Control'] = 'no-cache'
    elif path.startswith('/static/') or path.startswith('/proxy/image'):
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
                   'customers', 'chats', 'backup', 'dashboard', 'audit', 'users'},
    'content': {'pages', 'theme', 'menu', 'socials', 'seo', 'site', 'backup'},
    'orders': {'dashboard', 'customers', 'chats', 'integrations', 'backup'},
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
    ('/admin/api/chats', 'chats'),
    ('/admin/api/chat/', 'chats'),
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
    ('/admin/api/section-presets', 'pages'),
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
    ('/admin/api/sync-status', 'integrations'),
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


# Каноническое лого (файл с оригинала ekotoriya.ru) — единственный источник
# во всех точках: шапка, подвал, favicon, OG, админка. ?v= сбрасывает кэш
# у посетителей при смене версии (PG-4.4).
CANONICAL_LOGO = '/static/upload/CMax/b2e/04g8dplb3dqudbalhuo0ryren6a5dklv.svg'
LOGO_VERSION = '4'


def _versioned_logo(url):
    """Каноническому лого добавить ?v=<версия>, если версии ещё нет."""
    url = str(url or '').strip()
    if url and url.split('?')[0] == CANONICAL_LOGO and 'v=' not in url:
        return url + ('&' if '?' in url else '?') + 'v=' + LOGO_VERSION
    return url


def _page_section(page):
    """Раздел сайта по slug страницы: catalog__namatrasniki → 'namatrasniki'.

    Пустая строка — страница не привязана к разделу (показываются все товары).
    """
    slug = (page.get('slug') or '')
    m = re.match(r'^catalog__([a-z0-9]+?)(?:__|$)', slug)
    return m.group(1) if m else ''


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
    # Синхронизация контактов: в content_html (снапшоты перенесённого дизайна)
    # контакты захардкожены — подменяем на актуальные из настроек сайта.
    if page.get('content_html'):
        page = {**page, 'content_html': _sync_contacts_html(page['content_html'], site)}
    # Раздел сайта: на страницах разделов (catalog__namatrasniki и глубже)
    # показываем товары без назначенного раздела + товары этого раздела.
    page_section = _page_section(page)
    if page_section:
        all_products = [p for p in all_products
                        if not p.get('site_section') or p.get('site_section') == page_section]
    ctx = {
        'site': site,
        'page': page,
        'page_section': page_section,
        'menu': content.get('menu', []),
        'socials': content.get('socials', []),
        'wb_products': all_products,
        'wb_demo': wb_sync.load_json(wb_sync.PRODUCTS_FILE).get('demo', False),
        'recommended_products': _rec_block_items(popular),
        'seo_org': _seo_org(content, host_url),
        'seo_og_image': _absolute_url(_versioned_logo(site.get('logo')), host_url) or (host_url.rstrip('/') + '/static/noimage.png'),
        'seo_breadcrumbs': _seo_breadcrumbs(content, page, host_url),
        'seo_faq': _seo_faq_items(page),
        # canonical: поле страницы в приоритете, иначе — абсолютный URL текущей
        # страницы (SEO-1). Для карточки товара _product_page переопределяет.
        'seo_canonical': _absolute_url(page.get('canonical'), host_url)
                         or (host_url.rstrip('/') + request.path),
        'seo_product': None,
        # Число статей блога (счётчик в боковом меню категорий статей).
        'blog_articles_count': sum(1 for p in content.get('pages', [])
                                   if str(p.get('slug', '')).startswith('blog__sovety-pokupatelyam__')),
        # Одобренные отзывы покупателей — только на странице «Отзывы» (PG-6.3).
        'reviews_dynamic': (_approved_reviews_html()
                            if page.get('slug') == 'company__reviews' else ''),
        # Режим «Скрыть маркетплейсы» (8.3): скрываем бейджи WB/Ozon и ссылки.
        'hide_mp': bool(site.get('hide_marketplaces')),
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
    # Каталог-представление для главной и разделов каталога: единый блок
    # маркетплейса получает пагинацию (главная — по 12 товаров) и живую
    # панель фильтров (разделы — с предфильтром товаров раздела). Полный
    # каталог /catalog/ формирует своё представление в catalog_page().
    try:
        if page_section:
            ctx['catalog_view'] = _filtered_catalog(
                request.args, section=page_section, base=request.path,
                show_filters=True)
        elif (page.get('url') or '') == '/':
            ctx['catalog_view'] = _filtered_catalog(
                request.args, per_page=12, base='/', show_filters=False)
    except Exception:
        pass
    return ctx


@app.route('/')
def index():
    content = load_content()
    page = get_page_by_slug(content, 'index') or get_page_by_url(content, '/')
    if not page:
        return 'Index page not found', 404
    if _pe_bare_admin():
        # Контент для iframe редактора: с маркерами блоков, без кнопки админа.
        return Response(_inject_lazyload_fix(_render_page_html_marked(content, page)))
    html = render_template(page['template'], **make_context(content, page))
    return Response(_inject_lazyload_fix(_inject_admin_edit_button(html, f"/admin/page/{page['slug']}")))


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
    """Корзина из сессии: [{key, product, qty, price}] (LC-4).

    Позиции, которых нет в каталоге (например, товар из статичной aspro-карточки
    клона), достаются из cart_meta — клиент передаёт имя/цену с кнопки
    «Купить в 1 клик» (как /api/order, где цена тоже приходит от клиента)."""
    cart = session.get('cart') or {}
    meta = session.get('cart_meta') or {}
    products = get_products()
    by_key = {f"{p.get('source', 'wb')}:{p.get('nm_id') or p.get('id')}": p
              for p in products}
    items, total = [], 0
    for key, qty in cart.items():
        p = by_key.get(key)
        if p is None:
            m = meta.get(key)
            if not m:
                continue
            src, _, pid = key.partition(':')
            p = {'id': pid, 'nm_id': int(pid) if str(pid).isdigit() else 0,
                 'source': src, 'name': m.get('name') or f'Товар {pid}',
                 'final_price': int(m.get('price') or 0)}
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
    else:
        html = _inject_admin_edit_button(html, request.path + '?edit=1')
        html = _inject_lazyload_fix(html)
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
    # Товара нет в каталоге (карточки aspro-клона): имя/цена от клиента
    # сохраняются в cart_meta — та же механика, что в /api/cart/buy-one-click.
    known = any(f"{p.get('source', 'wb')}:{p.get('nm_id') or p.get('id')}" == key
                for p in get_products())
    meta = session.get('cart_meta') or {}
    if not known and data.get('product_name'):
        try:
            price = int(float(data.get('price') or 0))
        except (TypeError, ValueError):
            price = 0
        meta[key] = {'name': str(data.get('product_name'))[:1000],
                     'price': max(0, price)}
    else:
        meta.pop(key, None)
    session['cart_meta'] = meta
    cart = session.get('cart') or {}
    cart[key] = int(cart.get(key, 0)) + qty
    session['cart'] = cart
    session.modified = True
    return jsonify({'success': True, 'cart': cart, 'count': sum(cart.values())})


@app.route('/api/cart/buy-one-click', methods=['POST'])
def api_cart_buy_one_click():
    """«Купить в 1 клик»: корзина очищается и в неё кладётся только этот товар,
    клиент идёт на /basket/ (оформление) — поля контактов там предзаполнены
    из профиля залогиненного клиента, но редактируемые."""
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
    # Товаров нет в каталоге (статичные aspro-карточки клона): сохраняем
    # имя/цену от клиента, чтобы корзина и заявка выглядели осмысленно.
    known = any(f"{p.get('source', 'wb')}:{p.get('nm_id') or p.get('id')}" == key
                for p in get_products())
    meta = session.get('cart_meta') or {}
    if not known and data.get('product_name'):
        try:
            price = int(float(data.get('price') or 0))
        except (TypeError, ValueError):
            price = 0
        meta[key] = {'name': str(data.get('product_name'))[:1000],
                     'price': max(0, price)}
    else:
        meta.pop(key, None)
    session['cart_meta'] = meta
    session['cart'] = {key: qty}
    session.modified = True
    return jsonify({'success': True, 'cart': session['cart'],
                    'redirect': '/basket/?oneclick=1'})


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
    session['cart_meta'] = {}
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
    # Избранное: лайкнутые товары. Живые данные из каталога, если товар там
    # есть; иначе — снапшот из события like (статичные карточки разделов).
    # Позиции без названия и данных не показываем (вместо «заглушек»).
    products = get_products()
    by_id = {str(p.get('nm_id') or p.get('id')): p for p in products}
    favorite_products = []
    for l in likes:
        pid = str(l['product_id'])
        p = by_id.get(pid)
        if p:
            item = dict(p)
            item['url'] = '/catalog/{}/{}/'.format(
                p.get('source') or 'wb', p.get('nm_id') or p.get('id'))
            favorite_products.append(item)
            continue
        snap = l.get('snapshot') or {}
        name = (snap.get('name') or l.get('product_name') or '').strip()
        if not name:
            continue
        favorite_products.append({
            'name': name,
            'photo': snap.get('photo') or '',
            'price': snap.get('price') or 0,
            'final_price': snap.get('price') or 0,
            'url': snap.get('url') or '',
            'source': snap.get('source') or '',
            'nm_id': l['product_id'],
            'from_snapshot': True,
        })
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
    else:
        html = _inject_admin_edit_button(html, request.path + '?edit=1')
        html = _inject_lazyload_fix(html)
    return Response(html)


@app.route('/api/client/profile', methods=['POST'])
@_client_required
def api_client_profile():
    """Обновление контактов клиента из ЛК (LC-3, этап 7.1: +телефон)."""
    data = request.get_json() or {}
    result = customers.update_customer_profile(
        session['customer_id'], name=data.get('name'), email=data.get('email'),
        phone=data.get('phone'))
    if isinstance(result, tuple):
        return jsonify({'success': False, 'error': result[1]}), 400
    if not result:
        return jsonify({'success': False, 'error': 'Клиент не найден'}), 404
    return jsonify({'success': True, 'customer': result})


@app.route('/api/client/password', methods=['POST'])
@_client_required
@_rate_limit(10)
def api_client_password():
    """Смена пароля из ЛК (этап 7.1): текущий + новый (≥6 символов)."""
    data = request.get_json() or {}
    updated, error = customers.change_client_password(
        session['customer_id'],
        data.get('current_password', ''),
        data.get('new_password', ''))
    if error:
        return jsonify({'success': False, 'error': error}), 400
    return jsonify({'success': True})


@app.route('/api/client/orders/<int:order_id>/repeat', methods=['POST'])
@_client_required
@_rate_limit(20)
def api_client_order_repeat(order_id):
    """Повтор заказа (этап 7.2): позиция заявки клиента уходит в корзину."""
    order = db.session.get(Order, order_id)
    if order is None or order.customer_id != session['customer_id']:
        return jsonify({'success': False, 'error': 'Заявка не найдена'}), 404
    pid = str(order.product_id or '').strip()
    if not pid:
        return jsonify({'success': False, 'error': 'В заявке нет товара'}), 400
    products = get_products()
    found = None
    for p in products:
        if str(p.get('nm_id') or p.get('id')) == pid:
            found = p
            break
    source = (found or {}).get('source') or 'wb'
    key = f'{source}:{pid}'
    meta = session.get('cart_meta') or {}
    if found is None:
        meta[key] = {'name': order.product_name or 'Товар',
                     'price': max(0, int(order.price or 0))}
    else:
        meta.pop(key, None)
    session['cart_meta'] = meta
    cart = session.get('cart') or {}
    cart[key] = int(cart.get(key, 0)) + max(1, int(order.qty or 1))
    session['cart'] = cart
    session.modified = True
    return jsonify({'success': True, 'cart': cart,
                    'count': sum(cart.values()), 'redirect': '/basket/'})


def _own_dialog(customer_id, create=False):
    """Диалог клиента (этап 7.3) или None; create=True — создать при отсутствии."""
    from models import ChatDialog
    dialog = db.session.query(ChatDialog).filter_by(
        customer_id=int(customer_id)).first()
    if dialog is None and create:
        dialog = ChatDialog(customer_id=int(customer_id), status='open')
        db.session.add(dialog)
        db.session.commit()
    return dialog


@app.route('/api/client/chat')
@_client_required
def api_client_chat_get():
    """История своего чата (этап 7.3); входящие от админа помечаются прочитанными."""
    from models import ChatMessage
    dialog = _own_dialog(session['customer_id'])
    if dialog is None:
        return jsonify({'success': True, 'dialog': None, 'messages': []})
    messages = db.session.query(ChatMessage).filter_by(
        dialog_id=dialog.id).order_by(ChatMessage.id.asc()).limit(500).all()
    changed = False
    for m in messages:
        if m.sender == 'admin' and not m.read_by_client:
            m.read_by_client = True
            changed = True
    if changed:
        db.session.commit()
    return jsonify({'success': True, 'dialog': dialog.to_dict(),
                    'messages': [m.to_dict() for m in messages]})


@app.route('/api/client/chat', methods=['POST'])
@_client_required
@_rate_limit(10)
def api_client_chat_send():
    """Клиент пишет в чат (этап 7.3): 1..2000 символов; закрытый диалог открывается."""
    from models import ChatMessage
    data = request.get_json() or {}
    text = str(data.get('text') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'Введите сообщение'}), 400
    if len(text) > 2000:
        return jsonify({'success': False, 'error': 'Сообщение слишком длинное'}), 400
    dialog = _own_dialog(session['customer_id'], create=True)
    if dialog.status != 'open':
        dialog.status = 'open'
    msg = ChatMessage(dialog_id=dialog.id, sender='client', text=text,
                      read_by_admin=False, read_by_client=True)
    db.session.add(msg)
    db.session.commit()
    return jsonify({'success': True, 'message': msg.to_dict()})


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
    else:
        html = _inject_admin_edit_button(html, request.path + '?edit=1')
        html = _inject_lazyload_fix(html)
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
    customers.bind_session_likes(customer['id'], _session_id())
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
    customers.bind_session_likes(customer['id'], _session_id())
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


def _theme_css_text(theme):
    """CSS-текст темы (CN-T7.4): общая генерация для /theme.css и предпросмотра.

    Безопасность значений дублируется даже при переданной теме (/_validated_theme
    на сервере + те же regex-фолбэки здесь).
    """
    theme = theme or {}
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
    return css + BLOCKS_CSS


@app.route('/theme.css')
def theme_css():
    """Generate CSS from site theme settings."""
    content = load_content()
    # Cache-Control ставит after_request (no-cache — ревалидация при каждом заходе)
    return Response(_theme_css_text(content.get('site', {}).get('theme', {})),
                    mimetype='text/css')


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

/* ---------- Голые текстовые блоки перенесённых страниц (ul/ol/p/h на верхнем
   уровне контента) — прижимались к границам страницы. Оформляем как контейнер.
   В шаблонах контент лежит либо прямо в #content, либо в обёртке .middle. ---------- */
#content > ul:not([class]),
#content > ol:not([class]),
#content > p:not([class]),
#content > h1:not([class]),
#content > h2:not([class]),
#content > h3:not([class]),
#content > h4:not([class]),
#content .middle > ul:not([class]),
#content .middle > ol:not([class]),
#content .middle > p:not([class]),
#content .middle > h1:not([class]),
#content .middle > h2:not([class]),
#content .middle > h3:not([class]),
#content .middle > h4:not([class]) {
    max-width: var(--theme-container-width, 1400px);
    margin-left: auto;
    margin-right: auto;
    padding-left: 24px;
    padding-right: 24px;
    box-sizing: border-box;
}
#content > ul:not([class]),
#content > ol:not([class]),
#content .middle > ul:not([class]),
#content .middle > ol:not([class]) { padding-left: 48px; margin-top: 1.2rem; margin-bottom: 1.2rem; }
#content > ul:not([class]) li,
#content > ol:not([class]) li,
#content .middle > ul:not([class]) li,
#content .middle > ol:not([class]) li { margin-bottom: 0.6rem; line-height: 1.6; }

/* ---------- Выпадающие меню навигации: аккуратный современный вид ---------- */
.table-menu .dropdown-menu,
#headerfixed .dropdown-menu {
    display: block;
    border: 0 !important;
    border-radius: 12px !important;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.16), 0 2px 8px rgba(0, 0, 0, 0.08) !important;
    padding: 8px !important;
    background: #fff !important;
    min-width: 220px;
    opacity: 0;
    visibility: hidden;
    transform: translateY(8px);
    transition: opacity 0.18s ease, transform 0.18s ease, visibility 0.18s ease;
    overflow: auto;
    max-height: 70vh;
}
.table-menu .dropdown:hover > .wrap > .dropdown-menu,
.table-menu .dropdown.open > .wrap > .dropdown-menu,
#headerfixed .dropdown:hover > .wrap > .dropdown-menu,
#headerfixed .dropdown.open > .wrap > .dropdown-menu {
    opacity: 1;
    visibility: visible;
    transform: translateY(0);
}
.table-menu .dropdown-menu .customScrollbar,
.table-menu .dropdown-menu .scrollblock { padding: 0 !important; }
.table-menu .dropdown-menu ul.menu-wrapper,
.table-menu .dropdown-menu ul.menu-wrapper > li {
    padding: 0 !important;
    margin: 0 !important;
    list-style: none;
}
.table-menu .dropdown-menu ul.menu-wrapper > li > a,
.table-menu .dropdown-menu .menu-item > a {
    display: block !important;
    padding: 10px 14px !important;
    margin: 2px 0 !important;
    border-radius: 8px !important;
    color: #333 !important;
    font-size: 0.95rem;
    line-height: 1.35;
    text-decoration: none;
    transition: background 0.12s ease, color 0.12s ease, padding-left 0.12s ease;
    background: transparent !important;
    border: 0 !important;
    position: relative;
}
.table-menu .dropdown-menu ul.menu-wrapper > li > a:hover,
.table-menu .dropdown-menu .menu-item > a:hover,
.table-menu .dropdown-menu ul.menu-wrapper > li.dropdown-submenu:hover > a {
    background: rgba(180, 157, 132, 0.14) !important;
    color: #7a5c3e !important;
    padding-left: 18px !important;
}
.table-menu .dropdown-menu ul.menu-wrapper > li > a .name.option-font-bold { font-weight: 600; }
/* Скроллбар выпадашки — в цветах темы */
.table-menu .dropdown-menu::-webkit-scrollbar,
#headerfixed .dropdown-menu::-webkit-scrollbar { width: 6px; }
.table-menu .dropdown-menu::-webkit-scrollbar-thumb,
#headerfixed .dropdown-menu::-webkit-scrollbar-thumb {
    background: #d8cfc4; border-radius: 3px;
}
.table-menu .dropdown-menu::-webkit-scrollbar-track,
#headerfixed .dropdown-menu::-webkit-scrollbar-track { background: transparent; }
/* Стрелочка-приглашение у пункта с подменю */
.table-menu .dropdown-menu li.dropdown-submenu > a::after {
    content: '›';
    position: absolute;
    right: 12px;
    top: 50%;
    transform: translateY(-50%);
    color: #b49d84;
    font-size: 1rem;
}
/* Подменю второго уровня */
.table-menu .dropdown-menu ul.dropdown-menu.toggle_menu {
    border-radius: 10px !important;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.14) !important;
    padding: 6px !important;
    background: #fff !important;
    border: 0 !important;
}
/* Плавное появление самого пункта меню при наведении на родителя */
.table-menu td.menu-item > .wrap > a > div { transition: color 0.15s ease; }
.table-menu td.menu-item.dropdown:hover > .wrap > a > div { color: #9a8269; }
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
        if _pe_bare_admin():
            return Response(_inject_lazyload_fix(_render_page_html_marked(content, page)))
        html = render_template(page['template'], **make_context(content, page))
        return Response(_inject_lazyload_fix(_inject_admin_edit_button(html, f"/admin/page/{page['slug']}")))
    # Try slug match (double underscores represent slashes)
    slug = path.replace('/', '__').replace('-', '_')
    page = get_page_by_slug(content, slug)
    if page and page.get('slug') not in SYSTEM_PAGE_SLUGS:
        if _pe_bare_admin():
            return Response(_inject_lazyload_fix(_render_page_html_marked(content, page)))
        html = render_template(page['template'], **make_context(content, page))
        return Response(_inject_lazyload_fix(_inject_admin_edit_button(html, f"/admin/page/{page['slug']}")))
    # Fallback: if a parent path exists (e.g. product detail link under /catalog/namatrasniki/),
    # redirect to the nearest parent page to avoid 404s from original product cards.
    parts = path.split('/')
    # Битрикс-URL товара с артикулом на конце (-<nm_id>): если такой товар есть
    # в живом каталоге — 301 на его рабочую страницу вместо «мёртвого» редиректа
    # на родительский раздел (закладки/прямые заходы/индексация).
    nid = re.search(r'(\d{6,})$', parts[-1])
    if nid:
        _by_nm, _ = _legacy_catalog_index()
        _src = _by_nm.get(nid.group(1))
        if _src:
            return redirect(f'/catalog/{_src}/{nid.group(1)}/', code=301)
    for i in range(len(parts) - 1, 0, -1):
        parent_url = '/' + '/'.join(parts[:i]) + '/'
        parent_page = get_page_by_url(content, parent_url)
        if parent_page:
            return redirect(parent_url, code=302)
    return page_not_found(None), 404


@app.errorhandler(404)
def page_not_found(e):
    """404 (CN-6): полный aspro-шаблон как на оригинале (блок ошибки со стрелкой
    «404», пояснением и кнопками); блоки системной страницы '404' при наличии
    редактируются тем же редактором «на странице» и заменяют блок ошибки."""
    content, page = _system_page('404')
    if not page.get('template'):
        page['template'] = 'pages/404.html'
        save_content(content)
    edit_mode = _system_edit_mode('404')
    edit_page = dict(page)
    if not edit_page.get('title') or edit_page.get('title') == '404':
        edit_page['title'] = 'Страница не найдена'
    blocks = page.get('blocks')
    if not isinstance(blocks, list):
        blocks = []
    edit_page['content_html'] = (blocks_module.render_blocks_marked(blocks)
                                 if edit_mode else blocks_module.render_blocks(blocks))
    html = render_template(page['template'], **make_context(content, edit_page))
    if edit_mode:
        html = _inject_editor(html, '404')
    else:
        html = _inject_admin_edit_button(html, request.path + '?edit=1')
        html = _inject_lazyload_fix(html)
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


def _section_catalog_base():
    """Базовый URL «все товары этого типа» для фикса пагинации категорий.

    Кнопка «Показать ещё» и номера страниц bitrix-пагинации на категорийных
    страницах (/catalog/<раздел>/) ведут на единый каталог /catalog/ с фильтром
    category раздела (если товары раздела с категорией), а не подгружают
    ajax'ом целую следующую страницу — bitrix section_pagination.js вставляет
    в DOM весь документ, включая шапку и подвал.
    """
    from collections import Counter
    from urllib.parse import quote
    m = re.match(r'^/catalog/([a-z0-9-]+)/', request.path or '')
    section = m.group(1) if m else ''
    products = list(get_products())
    if section:
        products = [p for p in products
                    if not p.get('site_section') or p.get('site_section') == section]
    cats = [str(p.get('category')) for p in products if p.get('category')]
    if cats:
        return ('/catalog/?category=' + quote(Counter(cats).most_common(1)[0][0])
                + '&per_page=all')
    return '/catalog/?per_page=all'


def _visitor_liked_ids():
    """Лайкнутые id товаров текущего посетителя (аккаунт или анонимная
    сессия — та же логика, что в /api/like). Передаются на клиент, чтобы
    после перезагрузки страницы кнопки «В избранное» и счётчик в шапке
    показывали актуальное состояние, а не захардкоженные нули."""
    try:
        likes = customers.get_visitor_likes(
            customer_id=session.get('customer_id'),
            session_id='' if session.get('customer_id') else _session_id())
        return [str(l['product_id']) for l in likes]
    except Exception:
        return []


# Ховер-блок карточек каталога («− 1 + В корзину»): оригинальный aspro выводил
# .footer_button абсолютом ниже карточки (top:100%) — блок наезжал на товары
# следующего ряда и появлялся мгновенно. Переопределение: полоса под кнопкой
# резервируется внутри карточки (компенсируем штатный padding-bottom 29px),
# блок плавно проявляется в границах карточки и не перекрывает соседние товары.
_HOVER_CSS = (
    '<style>'
    '@media (min-width:601px){'
    'html:not(.mobile) .catalog_block .item_block .catalog_item:not(.big) '
    '.inner_wrap>.item_info{padding-bottom:0 !important;}'
    'html:not(.mobile) .catalog_block .item_block .catalog_item:not(.big) '
    '.inner_wrap{padding-bottom:56px !important;}'
    'html:not(.mobile) .catalog_block .item_block .catalog_item:not(.big) '
    '.footer_button{position:absolute !important;left:0 !important;'
    'right:0 !important;top:auto !important;bottom:0 !important;'
    'display:block !important;height:auto !important;'
    'opacity:0 !important;visibility:hidden !important;'
    'transform:translateY(8px);'
    'transition:opacity .25s ease,transform .25s ease,'
    'visibility 0s linear .25s !important;'
    'box-shadow:none !important;z-index:3;}'
    'html:not(.mobile) .catalog_block .item_block .catalog_item:not(.big):hover '
    '.footer_button,'
    'html:not(.mobile) .catalog_block .item_block .catalog_item.hover '
    '.footer_button{opacity:1 !important;visibility:visible !important;'
    'transform:translateY(0);'
    'transition:opacity .25s ease,transform .25s ease,visibility 0s !important;}'
    '}'
    '</style>'
)

# Состояния кнопки избранного: только CSS-класс (без inline-стилей из JS —
# они затирали цвета темы, и после снятия лайка кнопка оставалась чёрной).
# «Не в избранном» — контур акцентного цвета; «в избранном» — заливка акцентом.
LIKED_BTN_CSS = (
    '<style>'
    '.wb-like-btn{color:#c00 !important;border:1px solid #c00 !important;'
    'background:#fff !important;}'
    '.wb-like-btn.wb-liked-btn{background:#c00 !important;'
    'color:#fff !important;border-color:#c00 !important;}'
    '</style>'
)

# Левое меню категорий (.left_block, разметка bitrix-клона): у страницы нет
# колонки-контейнера (body без block_side_WIDE/side_LEFT), поэтому
# sticky-sidebar прибивал меню к левому краю viewport и выпадашка «Каталог»
# висела поверх контента. Список свёрнут по умолчанию (раскрытие — hover
# или клик по шапке, см. инжект-скрипт), приклеивание отключено.
LEFTMENU_CSS = (
    '<style>'
    '.left_block .menu_top_block .slide-block__body{display:none !important;}'
    '.left_block .menu_top_block.opened .slide-block__body,'
    '.left_block .menu_top_block:hover .slide-block__body{display:block !important;}'
    '.left_block .menu_top_block .slide-block__head{cursor:pointer;}'
    '.left_block.is-affixed .sticky-sidebar__inner{position:static !important;'
    'transform:none !important;top:auto !important;left:auto !important;}'
    '</style>'
)

# Вёрстка главной страницы каталога (/catalog/): ряд плиток подкатегорий
# (.catalog_section_list) был full-bleed и на широких экранах «прилипал»
# к краям, тогда как фильтры, SEO-тексты и товарная сетка лежат в
# .maxwidth-theme (max-width:1408px, по центру). Ограничиваем тот же ряд
# тем же контейнером: max-width/margin срабатывают только когда viewport
# шире 1408px, боковой padding — только на широких экранах, мобильная
# вёрстка (≤1408px) не затрагивается. Скоуп .main-catalog-wrapper есть
# только на /catalog/ (у категорийных страниц его нет).
CATALOG_LAYOUT_CSS = (
    '<style>'
    '.catalog_page .main-catalog-wrapper .catalog_section_list{'
    'max-width:1408px !important;margin-left:auto !important;'
    'margin-right:auto !important;}'
    '@media (min-width:1409px){'
    '.catalog_page .main-catalog-wrapper .catalog_section_list{'
    'padding-left:16px !important;padding-right:16px !important;}'
    '}'
    '</style>'
)

# Вёрстка категорийных страниц (/catalog/<раздел>/): строка сортировки и
# панель bitrix-smartfilter (.filter-panel-wrapper) были full-bleed и
# «прилипали» к краям (x=0 без боковых отступов). Тот же контейнер 1408px,
# что и на /catalog/: max-width/margin — самоактивны только >1408px, padding
# — только на широких экранах. Скоуп .with_left_block есть только у
# категорийных страниц (на /catalog/ его нет — главная каталога не трогается).
SECTION_FILTER_CSS = (
    '<style>'
    '.wrapper1.with_left_block .filter-panel-wrapper{'
    'max-width:1408px !important;margin-left:auto !important;'
    'margin-right:auto !important;}'
    '@media (min-width:1409px){'
    '.wrapper1.with_left_block .filter-panel-wrapper{'
    'padding-left:16px !important;padding-right:16px !important;}'
    '}'
    '</style>'
)

# Категорийные страницы: мёртвый bitrix-листинг скрыт — вместо него над снапшотом
# выводится живой блок маркетплейс (панель /api/catalog + сетка товаров раздела).
# .section-content-wrapper есть только в снапшотах страниц разделов; рабочие
# элементы (левое меню, SEO-тексты под листингом) не затрагиваются.
SECTION_LISTING_HIDE_CSS = (
    '<style>'
    '.section-content-wrapper .filter-panel-wrapper{display:none !important;}'
    '.section-content-wrapper .catalog_block{display:none !important;}'
    '.section-content-wrapper .bottom_nav{display:none !important;}'
    '</style>'
)

# Раскладка категорийных страниц (/catalog/<раздел>/) как у оригинала: живой
# каталог (.wb-catalog-wrapper) встаёт в правую колонку рядом с левым меню
# раздела (.left_block bitrix-клона), а не полной шириной под ним. Дочерние
# элементы #content .middle: section_header → wb-catalog-wrapper →
# section-content-wrapper (мёртвый листинг, скрыт) → left_block.
# Меню развёрнуто до первого уровня (Наматрасники › / Одеяла › / Наперники,
# активный — подсвечен), подразделы раскрываются по hover — как в оригинале.
# Скоуп .section-content-wrapper есть только на страницах разделов.
SECTION_LAYOUT_CSS = (
    '<style>'
    '@media (min-width:992px){'
    '#content .middle{display:grid !important;'
    'grid-template-columns:262px minmax(0,1fr) !important;'
    'column-gap:32px !important;align-items:start !important;}'
    '#content .middle>.maxwidth-theme.wb-section-header{grid-column:1/-1;}'
    '#content .middle>.wb-catalog-wrapper{grid-column:2;grid-row:2;'
    'padding-top:0 !important;}'
    '#content .middle>.left_block{grid-column:1;grid-row:2;'
    'width:100% !important;height:auto !important;position:static !important;}'
    '#content .middle>.left_block .sticky-sidebar__inner{'
    'position:static !important;width:auto !important;}'
    '#content .middle>.section-content-wrapper{grid-column:1/-1;}'
    '}'
    '@media (max-width:991px){'
    '#content .middle>.left_block{display:none !important;}'
    '}'
    '#content .middle>.left_block .slide-block__body{display:block !important;}'
    '#content .middle>.left_block li.has-child{position:relative;}'
    '#content .middle>.left_block li.has-child>ul.dropdown{'
    'display:none !important;position:absolute !important;left:100% !important;'
    'top:0 !important;width:250px !important;background:#fff !important;'
    'border:1px solid #eee !important;'
    'box-shadow:0 8px 24px rgba(0,0,0,.08) !important;'
    'z-index:60 !important;padding:.4rem 0 !important;margin:0 !important;'
    'max-height:none !important;overflow:visible !important;}'
    '#content .middle>.left_block li.has-child:hover>ul.dropdown{'
    'display:block !important;}'
    '#content .middle>.left_block li.has-child>ul.dropdown>li{'
    'float:none !important;display:block !important;width:auto !important;}'
    '#content .middle>.left_block li.has-child>ul.dropdown .image{'
    'display:none !important;}'
    '#content .middle>.left_block li.has-child>ul.dropdown li>a{'
    'display:block !important;padding:.5rem 1rem !important;'
    'font-size:.85rem !important;font-weight:400 !important;'
    'white-space:normal !important;border:none !important;}'
    '#content .middle>.left_block a.bordered{border:none !important;'
    'border-radius:0 !important;}'
    '</style>'
)

# Иконки соцсетей в выдвижной мобильной панели (#mobilemenu, «сайдбар» на
# мобильных) шли общими aspro-правилами .social-icons li a: квадратные
# (border-radius:3px), без обводки, всегда белые иконки (верхняя строка
# спрайта) и hover opacity:.8. Футерные кнопки (footer .social-icons) —
# круглые (50%), с обводкой, приглушены до opacity:.5 и при наведении
# полностью проявляются (opacity:1, обводка исчезает, иконка переключается
# на «брендовый» ряд спрайта). Панель мобильного меню БЕЛАЯ (у футера тёмный
# фон), поэтому прямой перенос футерных правил делает кнопки невидимыми
# (светлый ряд спрайта на белом). Берём aspro-вариант «light» того же
# футерного стиля: те же круги/обводка/поведение, но тёмный ряд спрайта
# (-565/-612/-659/-706, см. footer .light ... в template-стилях) и обводка
# rgba(51,51,51,.2) — видно на белой панели. Позиции для всех сетей, которые
# может добавить админка (шаблон site_socials.html). Скоуп #mobilemenu:
# футер, шапка и прочие .social-icons не затрагиваются.
_SOCIAL_POS = {
    # name: (покой — «тёмный» ряд спрайта для светлого фона, hover — «брендовый» ряд)
    'vk': ('0px -565px', '0px -188px'),
    'facebook': ('-95px -565px', '-94px -188px'),
    'twitter': ('0px -612px', '0px -235px'),
    'instagram': ('-142px -706px', '-141px -329px'),
    'telegram': ('-330px -706px', '-329px -329px'),
    'ytb': ('-95px -706px', '-94px -329px'),
    'rtb': ('-613px -564px', '-611px -188px'),
    'max': ('-614px -611px', '-611px -234px'),
    'odn': ('-48px -565px', '-47px -188px'),
    'gplus': ('-48px -706px', '-47px -329px'),
    'mail': ('-377px -612px', '-376px -235px'),
    'viber': ('-330px -565px', '-329px -188px'),
    'whats': ('-330px -612px', '-329px -235px'),
    'zen': ('-471px -706px', '-470px -329px'),
    'tiktok': ('-518px -706px', '-517px -329px'),
    'pinterest': ('-189px -565px', '-188px -188px'),
    'snapchat': ('-518px -659px', '-517px -282px'),
    'linkedin': ('-95px -659px', '-94px -282px'),
}
SOCIALS_CSS = (
    '<style>'
    '#mobilemenu .social-icons ul li a{'
    'width:43px !important;border-radius:50% !important;'
    'opacity:.5 !important;border:1px solid rgba(51,51,51,0.2) !important;'
    'transition:opacity .3s ease !important;}'
    '#mobilemenu .social-icons ul li a:hover{'
    'opacity:1 !important;border:none !important;}'
    + ''.join(
        '#mobilemenu .social-icons ul li.{0} a{{background-position:{1} !important;}}'
        '#mobilemenu .social-icons ul li.{0} a:hover{{background-position:{2} !important;}}'.format(
            name, rest, hover)
        for name, (rest, hover) in _SOCIAL_POS.items()
    )
    + '</style>'
)

# ---------- наследие битрикс-каталога: мёртвые ссылки карточек ----------

# Статичные aspro-карточки на категорийных страницах (снимок дизайна
# оригинального магазина) ссылаются на битрикс-URL товаров, которых в этом
# каталоге нет: клик уходил в page_route и 302-редиректил на родительский
# раздел — страница просто обновлялась. Два уровня починки:
#  1) Серверный рерайт HTML (_rewrite_legacy_product_links, ниже): ссылка
#     с артикулом на конце (-<nm_id>) ведёт на рабочую страницу товара
#     /catalog/<source>/<nm_id>/; ссылка без артикула сопоставляется по
#     имени карточки с товарами каталога (нормализация + стемминг + доля
#     совпавших токенов); совпала — тоже рерайт. Не совпала — href="#"
#     + data-legacy-product (открывает быструю карточку-модалку, JS ниже).
#  2) 301-редирект в page_route для прямых заходов/закладок на -<nm_id>.
_LEGACY_LINK_RE = re.compile(
    r'<a\b[^>]*?\shref="(/catalog/[^"#?]+/)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
# Консервативный стеммер: срезаем одно характерное русское окончание
# («резинке»/«резинках» → «резинк»), чтобы имена битрикс-карточек и товаров
# каталога («на резинке» vs «на резинках») давали совпадения.
_STEM_SUFFIXES = ('ами', 'ями', 'ах', 'ях', 'ого', 'ому', 'ого', 'ыми',
                  'ов', 'ев', 'ей', 'ий', 'ый', 'ой', 'ая', 'ое', 'ие',
                  'ые', 'ую', 'юю', 'ам', 'ям', 'ем', 'ом', 'ью',
                  'у', 'е', 'и', 'а', 'я', 'о', 'й', 'ю')
_TOKEN_RE = re.compile(r'[a-zа-яё0-9]+')


def _stem(tok):
    for suf in _STEM_SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[:-len(suf)]
    return tok


def _norm_tokens(text):
    # '160x200' (латинская x) и '160х200' (кириллическая) — одно и то же
    text = re.sub(r'(?<=\d)[xX×](?=\d)', 'х', (text or '').lower())
    return [_stem(t) for t in _TOKEN_RE.findall(text)
            if len(t) > 2 or t.isdigit()]


def _tok_weight(tokens):
    # Токены с цифрами (размеры 140х200, высота 21) — главные дискриминаторы.
    return sum(2 if any(ch.isdigit() for ch in t) else 1 for t in tokens)


@cache.memoize(timeout=300)
def _legacy_catalog_index():
    """{nm_id: source} + [(nm_id, source, stemmed-name-token-set)] для
    сопоставления мёртвых ссылок с живым каталогом (WB/Ozon)."""
    by_nm, names = {}, []
    for p in get_products():
        nm = str(p.get('nm_id') or p.get('id') or '')
        src = p.get('source') or 'wb'
        if not nm:
            continue
        by_nm[nm] = src
        tk = set(_norm_tokens(p.get('name') or ''))
        if tk:
            names.append((nm, src, tk))
    return by_nm, names


@cache.memoize(timeout=300)
def _page_url_set():
    """Множество URL существующих страниц: ссылки-подразделы (/catalog/<раздел>/
    <подраздел>/) существуют и рерайтить их нельзя."""
    urls = set()
    for p in load_content().get('pages', []):
        u = p.get('url')
        if u:
            urls.add(u if u.endswith('/') else u + '/')
    return urls


def _match_legacy_product(tokens, names):
    """Лучший товар каталога для набора токенов имени карточки.

    Доля веса совпавших токенов во весе запроса; токены с цифрами (размеры)
    весят вдвое больше — иначе все карточки «непромокаемых на молнии»
    схлопывались в один товар. Нужно ≥2 общих токена и ≥0.5 веса.
    """
    if not tokens:
        return None
    ts = set(tokens)
    qw = _tok_weight(ts) or 1
    best, best_score = None, 0.0
    for nm, src, pset in names:
        inter = ts & pset
        if len(inter) < 2:
            continue
        score = _tok_weight(inter) / float(qw)
        if score > best_score:
            best, best_score = (nm, src), score
    if best and best_score >= 0.5:
        return best
    return None


def _rewrite_legacy_product_links(html):
    """Рерайт мёртвых битрикс-ссылок карточек (см. пояснение у _LEGACY_LINK_RE)."""
    by_nm, names = _legacy_catalog_index()
    if not by_nm:
        return html
    page_urls = _page_url_set()
    out = html
    for m in reversed(list(_LEGACY_LINK_RE.finditer(html))):
        href = m.group(1)
        if re.match(r'^/catalog/(wb|ozon|site)/\d+/?$', href):
            continue
        if href in page_urls:
            continue
        seg = [s for s in href.split('/') if s]
        if len(seg) < 3:
            continue
        new_href, legacy = None, False
        nid = re.search(r'(\d{6,})$', seg[-1])
        if nid and nid.group(1) in by_nm:
            new_href = '/catalog/{}/{}/'.format(by_nm[nid.group(1)], nid.group(1))
        else:
            text = re.sub(r'<[^>]+>', ' ', m.group(2))
            hit = _match_legacy_product(_norm_tokens(text), names)
            if hit:
                new_href = '/catalog/{}/{}/'.format(hit[1], hit[0])
            else:
                new_href, legacy = '#', True
        whole = m.group(0)
        gt = whole.find('>')
        open_tag = whole[:gt + 1]
        fixed = re.sub(r'(\shref=")[^"]*(")',
                       lambda mm: mm.group(1) + new_href + mm.group(2),
                       open_tag, count=1)
        if legacy:
            fixed = (fixed[:-1] + ' data-legacy-product="1"'
                     + ' data-legacy-href="' + href + '">')
        out = out[:m.start()] + fixed + whole[gt + 1:] + out[m.end():]
    return out


# Быстрая карточка (модалка) для мёртвой ссылки, которую не удалось
# сопоставить с каталогом: фото/имя/цена берутся из DOM-карточки, «В корзину»
# и «Купить в 1 клик» живут на INLINE-перехвате (страницы не gzip'уются и
# доходят целиком; внешние JS у части клиентов обрезаны — см. CART_INTERCEPT_JS).
LEGACY_MODAL_CSS = (
    '<style>'
    '#legacy-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);'
    'z-index:10050;display:none;align-items:center;justify-content:center;'
    'padding:16px;}'
    '#legacy-modal-overlay.open{display:flex;}'
    '#legacy-modal{background:#fff;max-width:420px;width:100%;border-radius:8px;'
    'padding:28px 24px 24px;position:relative;text-align:center;'
    'box-shadow:0 12px 40px rgba(0,0,0,.25);}'
    '#legacy-modal .lm-close{position:absolute;top:8px;right:12px;border:none;'
    'background:none;font-size:26px;line-height:1;cursor:pointer;color:#999;}'
    '#legacy-modal .lm-img{max-width:220px;max-height:220px;object-fit:contain;'
    'margin:0 auto 14px;display:block;}'
    '#legacy-modal .lm-name{font-size:17px;font-weight:600;margin:0 0 8px;}'
    '#legacy-modal .lm-price{font-size:20px;color:#b49d84;font-weight:700;'
    'margin:0 0 18px;}'
    '#legacy-modal .lm-actions{display:flex;flex-direction:column;gap:10px;}'
    '#legacy-modal .lm-btn{display:block;padding:13px 18px;border-radius:4px;'
    'font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;'
    'border:none;width:100%;box-sizing:border-box;}'
    '#legacy-modal .lm-cart{background:#b49d84;color:#fff;}'
    '#legacy-modal .lm-oneclick{background:#fff;color:#b49d84;'
    'border:1px solid #b49d84;}'
    '#legacy-modal .lm-all{background:none;color:#666;font-weight:400;'
    'text-decoration:underline;}'
    '</style>'
)

LEGACY_MODAL_JS = (
    '<script>(function(){'
    "if(window.__legacyModal)return;window.__legacyModal=true;"
    'function cardOf(a){return a.closest?a.closest('
    "'.catalog_item_wrapp,.item_block,.item_wrap,li,.product_image'):null;}"
    'function nameOf(a,c){'
    "var t=c&&c.querySelector('.item-title a');"
    'var n=(t&&t.textContent.trim())||a.textContent.trim()||"Товар Экотория";'
    'return n.slice(0,200);}'
    'function imgOf(c){var im=c&&c.querySelector("img");'
    'if(!im)return "";'
    "return im.currentSrc||im.src||im.getAttribute('data-src')||'';}"
    'function priceOf(c){if(!c)return 0;'
    "var p=c.querySelector('.cost .price[data-value]:not(.discount)')"
    "||c.querySelector('.cost .price[data-value]');"
    'if(p){var v=parseFloat(p.getAttribute("data-value"));'
    'if(!isNaN(v))return Math.round(v);}'
    "var t=c.querySelector('.price');"
    'if(t){var d=parseFloat(t.textContent.replace(/[^\\d.]/g,""));'
    'if(!isNaN(d))return Math.round(d);}return 0;}'
    'function fmt(n){return n>0?String(n).replace(/\\B(?=(\\d{3})+(?!\\d))/g," ")+" ₽":"Цену уточняйте";}'
    'function setCounter(n){'
    "document.querySelectorAll('a.basket-link.basket .js-count')"
    '.forEach(function(x){x.textContent=String(n);'
    "x.classList.toggle('empted',n===0);});}"
    'function post(url,body,done,btn,orig){'
    'var ctrl=new AbortController();'
    'var timer=setTimeout(function(){ctrl.abort();},10000);'
    "fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},"
    'body:JSON.stringify(body),signal:ctrl.signal})'
    '.then(function(r){clearTimeout(timer);if(!r.ok)throw 0;return r.json();})'
    '.then(function(d){if(!d.success)throw 0;done(d);})'
    '.catch(function(){clearTimeout(timer);'
    "btn.textContent='Не удалось, повторите';"
    'setTimeout(function(){btn.textContent=orig;},3000);});}'
    'function openModal(a){'
    'var c=cardOf(a);'
    'var name=nameOf(a,c),img=imgOf(c),price=priceOf(c);'
    'var old=document.getElementById("legacy-modal-overlay");'
    'if(old)old.remove();'
    'var ov=document.createElement("div");'
    'ov.id="legacy-modal-overlay";ov.className="open";'
    'ov.innerHTML='
    "'<div id=\"legacy-modal\" role=\"dialog\" aria-modal=\"true\">'"
    "+'<button class=\"lm-close\" type=\"button\" aria-label=\"Закрыть\">&times;</button>'"
    "+(img?'<img class=\"lm-img\" src=\"'+img+'\" alt=\"\">':'')"
    "+'<p class=\"lm-name\"></p>'"
    "+'<p class=\"lm-price\">'+fmt(price)+'</p>'"
    "+'<div class=\"lm-actions\">'"
    "+'<button class=\"lm-btn lm-cart\" type=\"button\">В корзину</button>'"
    "+'<button class=\"lm-btn lm-oneclick\" type=\"button\">Купить в 1 клик</button>'"
    "+'<a class=\"lm-btn lm-all\" href=\"/catalog/\">Все товары каталога</a>'"
    "+'</div></div>';"
    'ov.querySelector(".lm-name").textContent=name;'
    'ov.querySelector(".lm-close").onclick=function(){ov.remove();};'
    'ov.addEventListener("click",function(e){if(e.target===ov)ov.remove();});'
    'document.addEventListener("keydown",function esc(e){'
    'if(e.key==="Escape"){ov.remove();'
    'document.removeEventListener("keydown",esc);}});'
    'var body={product_id:"legacy-"+encodeURIComponent(name).slice(0,60),'
    "source:'wb',product_name:name,price:price};"
    'var cart=ov.querySelector(".lm-cart");'
    'cart.onclick=function(){'
    "var orig=cart.textContent;cart.textContent='Добавляем…';"
    "post('/api/cart/add',body,function(d){"
    "cart.textContent='✓ В корзине ('+d.count+')';setCounter(d.count);},"
    'cart,orig);};'
    'var one=ov.querySelector(".lm-oneclick");'
    'one.onclick=function(){'
    "var orig=one.textContent;one.textContent='Оформляем…';"
    "post('/api/cart/buy-one-click',body,function(d){"
    'location.href=d.redirect||"/basket/";},one,orig);};'
    'document.body.appendChild(ov);}'
    "document.addEventListener('click',function(e){"
    "var a=e.target.closest&&e.target.closest('a[data-legacy-product]');"
    'if(!a)return;'
    'e.preventDefault();e.stopPropagation();openModal(a);'
    '},true);'
    '})();</script>'
)


# INLINE в HTML, а не во внешнем wb-actions.js: страницы отдаются без gzip
# (в отличие от статики) и доходят до клиента целиком, тогда как внешние
# JS-файлы у части клиентов приходят обрезанными в конце (прокси/DPI портят
# gzip-поток: обрезанный файл = SyntaxError, весь wb-actions.js молча не
# выполняется — aspro-кнопка уходила в вечный спиннер, товар не добавлялся,
# ответа об ошибке нет). wb-actions.js при живом флаге window.__wbCartIntercept
# свои обработчики корзины не регистрирует (двойного добавления нет).
# Устойчивость: блокировка повторного клика, AbortController ~10 c с
# понятным сообщением вместо вечного спиннера, проверка r.ok до r.json(),
# редирект в /basket/ только при success.
CART_INTERCEPT_JS = (
    '<script>(function(){'
    'if(window.__wbCartIntercept)return;'
    'window.__wbCartIntercept=true;'
    'function cardOf(el){return el.closest?el.closest('
    "'.catalog_item_wrapp,.item_block,.catalog-block__item,.table-view__item,"
    ".product-main,.product-container,.lk-product'):null;}"
    'function pidOf(el){'
    'var c=cardOf(el)||el.parentElement;'
    "if(!c)return '';"
    "var own=c.getAttribute('data-id')||c.getAttribute('data-product-id')||'';"
    "if(own&&/^\\d+$/.test(own))return own;"
    'var ls=c.querySelectorAll(\'a[href]\'),i,m;'
    "for(i=0;i<ls.length;i++){m=(ls[i].pathname||'').match(/\\/catalog\\/[a-z]+\\/(\\d+)\\/?$/i);if(m)return m[1];}"
    "for(i=0;i<ls.length;i++){m=(ls[i].pathname||'').match(/(\\d{6,})\\/?$/);if(m)return m[1];}"
    "for(i=0;i<ls.length;i++){m=(ls[i].pathname||'').match(/(\\d+)\\/?$/);if(m)return m[1];}"
    "return '';}"
    'function nameOf(el){'
    "var c=cardOf(el);if(!c)return '';"
    "var sels=['.item-title','.product-title','h1','.lk-product .name'],i,j;"
    'for(i=0;i<sels.length;i++){'
    'var ns=c.querySelectorAll(sels[i]);'
    'for(j=0;j<ns.length;j++){'
    'var t=ns[j].textContent.trim();'
    'if(t)return t.slice(0,200);}}'
    "var dn=c.querySelector('[data-name]');"
    "if(!dn)return '';"
    "return (dn.getAttribute('data-name')||dn.textContent.trim()).slice(0,200);}"
    'function priceOf(el){'
    'var c=cardOf(el);if(!c)return 0;'
    "var p=c.querySelector('.cost .price[data-value]:not(.discount)')"
    "||c.querySelector('.cost .price[data-value]')"
    "||c.querySelector('[data-value]');"
    'if(p){var v=parseFloat(p.getAttribute(\'data-value\'));'
    'if(!isNaN(v))return Math.round(v);}'
    "var t=c.querySelector('.lk-product .price,.price');"
    'if(t){var d=parseFloat(t.textContent.replace(/[^\\d.]/g,\'\'));'
    'if(!isNaN(d))return Math.round(d);}'
    'return 0;}'
    'function setCounter(n){'
    "document.querySelectorAll('a.basket-link.basket .js-count')"
    '.forEach(function(x){x.textContent=String(n);'
    "x.classList.toggle('empted',n===0);});}"
    'function fail(btn,orig,msg){'
    'btn.textContent=msg;btn.style.color=\'#c00\';'
    'setTimeout(function(){btn.innerHTML=orig;btn.style.color=\'\';},3000);}'
    'function add(btn,body,done){'
    'if(btn.__cartBusy)return;'
    'btn.__cartBusy=true;'
    'var orig=btn.innerHTML;'
    "btn.textContent='Добавляем…';"
    'var ctrl=new AbortController();'
    'var timer=setTimeout(function(){ctrl.abort();},10000);'
    "fetch('/api/cart/add',{method:'POST',"
    "headers:{'Content-Type':'application/json'},"
    'body:JSON.stringify(body),signal:ctrl.signal})'
    '.then(function(r){'
    'clearTimeout(timer);'
    "if(!r.ok)throw new Error('HTTP '+r.status);"
    'return r.json();})'
    '.then(function(d){'
    "if(!d.success)throw new Error(d.error||'error');"
    'done(d);})'
    '.catch(function(err){'
    'clearTimeout(timer);'
    'btn.__cartBusy=false;'
    "fail(btn,orig,err.name==='AbortError'?"
    "'Нет ответа сервера, повторите':'Не удалось добавить');"
    '});}'
    "document.addEventListener('click',function(e){"
    "var wb=e.target.closest&&e.target.closest('.wb-cart-btn');"
    "var tc=e.target.closest&&e.target.closest('.to-cart');"
    'if(!wb&&!tc)return;'
    'e.preventDefault();e.stopPropagation();'
    'if(wb){'
    "add(wb,{product_id:wb.dataset.id,source:wb.dataset.source||'wb'},"
    'function(d){'
    "wb.textContent='✓ В корзине ('+d.count+')';"
    "setTimeout(function(){location.href='/basket/';},500);});"
    'return;}'
    "var bid=tc.getAttribute('data-item');"
    'var pid=pidOf(tc)||bid;'
    "if(!pid){fail(tc,tc.innerHTML,'Товар не найден');return;}"
    "var name='';"
    "var block=tc.closest('.button_block,.buy_block,.footer_button')||tc.parentElement;"
    "var ne=block&&block.querySelector('[data-js-item-name]');"
    "if(ne)name=(ne.getAttribute('data-js-item-name')||'').trim();"
    "if(!name)name=nameOf(tc);"
    "var price=parseInt(tc.getAttribute('data-value'),10)||priceOf(tc)||0;"
    "add(tc,{product_id:pid,source:'aspro',product_name:name,price:price},"
    'function(d){'
    'setCounter(d.count);'
    'document.querySelectorAll(\'.to-cart[data-item="\'+bid+\'"]\')'
    '.forEach(function(b){b.style.display=\'none\';});'
    'document.querySelectorAll(\'.in-cart[data-item="\'+bid+\'"]\')'
    '.forEach(function(a){a.style.display=\'\';});'
    "setTimeout(function(){location.href='/basket/';},500);});"
    '},true);'
    '})();</script>'
)


# Спиннер и состояние загрузки живого каталога: на время ajax-запроса сетка
# затемняется и поверх крутится индикатор (CATALOG_LIVE_JS добавляет/снимает
# .wb-loading на контейнере .wb-catalog-results).
CATALOG_LIVE_CSS = (
    '<style>'
    '.wb-catalog-results{position:relative;min-height:120px;}'
    '.wb-catalog-results.wb-loading{opacity:.45;pointer-events:none;}'
    '.wb-catalog-results.wb-loading::after{'
    'content:"";position:absolute;top:2rem;left:50%;width:34px;height:34px;'
    'margin-left:-17px;border:3px solid #d8cfc4;border-top-color:#b49d84;'
    'border-radius:50%;animation:wb-spin .8s linear infinite;}'
    '@keyframes wb-spin{to{transform:rotate(360deg);}}'
    '</style>'
)

# Живые фильтры каталога и ajax-пагинация блока маркетплейс: изменение любого
# контрола form.wb-catalog-filters (с дебаунсом 400 мс для текстовых полей)
# перезапрашивает частичный HTML через /api/catalog и подменяет контейнер
# .wb-catalog-results без перезагрузки страницы; URL обновляется pushState.
# Клики по пагинации перехватываются и уходят тем же путём. В iframe редактора
# pushState отключён. После подмены разметки кидается событие
# wb:results-updated — wb-actions.js восстанавливает состояние кнопок.
# JS формы подписки на рассылку в подвале (инжектится в _inject_lazyload_fix):
# submit без перезагрузки → POST /api/subscribe → сообщение рядом с формой.
SUBSCRIBE_JS = (
    '<script>(function(){'
    'if(window.__wbSubscribe)return;window.__wbSubscribe=1;'
    "document.addEventListener('submit',function(e){"
    'var f=e.target;'
    "if(!f||!f.classList||!f.classList.contains('wb-subscribe-form'))return;"
    'e.preventDefault();'
    "var btn=f.querySelector('[type=submit]');"
    "var msg=f.parentElement?f.parentElement.querySelector('.wb-subscribe-msg'):null;"
    'if(btn)btn.disabled=true;'
    "fetch('/api/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({email:(f.email&&f.email.value||'').trim(),source:f.dataset.source||'footer'})})"
    '.then(function(r){return r.json().then(function(d){return{status:r.status,data:d};});})'
    '.then(function(res){'
    'var d=res.data||{};'
    "if(msg){msg.style.display='block';"
    "msg.textContent=d.message||(res.status===400?'Проверьте email и попробуйте ещё раз':'Не удалось подписаться. Попробуйте позже.');}"
    "if(d&&d.success){f.reset();}"
    '})'
    ".catch(function(){if(msg){msg.style.display='block';"
    "msg.textContent='Не удалось подписаться. Попробуйте позже.';}})"
    '.finally(function(){if(btn)btn.disabled=false;});'
    '},false);'
    '})();</script>'
)

# Кнопки конструктора с действиями (цикл 4 / этап 5): клик по ссылке с
# data-wb-ctor-action открывает попап — форма заявки (POST /api/feedback),
# форма подписки (POST /api/subscribe) или текст из .wb-ctor-popup-content
# рядом с кнопкой (действие «попап»). Закрытие по крестику/оверлею/Esc.
CTOR_ACTION_CSS = (
    '<style>'
    '.wb-ctor-overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9998;}'
    '.wb-ctor-modal{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);'
    'background:#fff;border-radius:10px;max-width:440px;width:calc(100% - 32px);'
    'padding:28px 24px 24px;z-index:9999;box-shadow:0 12px 40px rgba(0,0,0,.25);}'
    '.wb-ctor-modal-close{position:absolute;top:8px;right:12px;border:0;background:none;'
    'font-size:22px;line-height:1;cursor:pointer;color:#999;}'
    '.wb-ctor-modal-title{margin:0 0 14px;font-size:1.25rem;}'
    '.wb-ctor-modal input[type=text],.wb-ctor-modal input[type=tel],'
    '.wb-ctor-modal input[type=email],.wb-ctor-modal textarea{width:100%;'
    'box-sizing:border-box;padding:10px 12px;margin-bottom:10px;border:1px solid #ddd;'
    'border-radius:6px;font-size:0.95rem;font-family:inherit;}'
    '.wb-ctor-modal textarea{min-height:90px;resize:vertical;}'
    '.wb-ctor-modal button[type=submit]{width:100%;padding:11px;border:0;border-radius:6px;'
    'background:#b49d84;color:#fff;font-size:1rem;font-weight:600;cursor:pointer;}'
    '.wb-ctor-modal .wb-ctor-msg{margin-top:10px;font-size:0.9rem;color:#666;}'
    '.wb-ctor-popup-title{font-size:1.2rem;font-weight:600;margin-bottom:10px;}'
    '.wb-ctor-popup-body{font-size:0.95rem;line-height:1.55;color:#333;}'
    '</style>'
)

CTOR_ACTION_JS = (
    '<script>(function(){'
    'if(window.__wbCtorAction)return;window.__wbCtorAction=1;'
    'function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c)'
    '{return{"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c];});}'
    'function close(){var o=document.querySelector(".wb-ctor-overlay"),'
    'm=document.querySelector(".wb-ctor-modal");'
    'if(o)o.remove();if(m)m.remove();}'
    'function open(html){close();'
    'var o=document.createElement("div");o.className="wb-ctor-overlay";'
    'var m=document.createElement("div");m.className="wb-ctor-modal";'
    'm.innerHTML=\'<button type="button" class="wb-ctor-modal-close" aria-label="Закрыть">&times;</button>\'+html;'
    'm.querySelector(".wb-ctor-modal-close").addEventListener("click",close);'
    'o.addEventListener("click",close);'
    'document.body.appendChild(o);document.body.appendChild(m);'
    'var f=m.querySelector("input");if(f)f.focus();}'
    'function bindFeedback(form){'
    'form.addEventListener("submit",function(e){e.preventDefault();'
    'var btn=form.querySelector("[type=submit]");if(btn)btn.disabled=true;'
    'var msg=form.parentElement.querySelector(".wb-ctor-msg");'
    "fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},"
    'body:JSON.stringify({name:form.fb_name.value,phone:form.fb_phone.value,'
    'message:form.fb_message?form.fb_message.value:"",'
    'page:location.pathname})})'
    '.then(function(r){return r.json().then(function(d){return{status:r.status,data:d};});})'
    '.then(function(res){var d=res.data||{};'
    'if(msg){msg.style.color=d.success?"#2e7d32":"#c62828";'
    'msg.textContent=d.message||(d.error||"Не удалось отправить. Попробуйте позже.");}'
    'if(d.success)form.reset();})'
    '.catch(function(){if(msg){msg.style.color="#c62828";'
    'msg.textContent="Не удалось отправить. Попробуйте позже.";}})'
    '.finally(function(){if(btn)btn.disabled=false;});});}'
    'function bindSubscribe(form){'
    'form.addEventListener("submit",function(e){e.preventDefault();'
    'var btn=form.querySelector("[type=submit]");if(btn)btn.disabled=true;'
    'var msg=form.parentElement.querySelector(".wb-ctor-msg");'
    "fetch('/api/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},"
    'body:JSON.stringify({email:form.email.value,source:"popup"})})'
    '.then(function(r){return r.json().then(function(d){return{status:r.status,data:d};});})'
    '.then(function(res){var d=res.data||{};'
    'if(msg){msg.style.color=d.success?"#2e7d32":"#c62828";'
    'msg.textContent=d.message||(d.error||"Не удалось подписаться. Попробуйте позже.");}'
    'if(d.success)form.reset();})'
    '.catch(function(){if(msg){msg.style.color="#c62828";'
    'msg.textContent="Не удалось подписаться. Попробуйте позже.";}})'
    '.finally(function(){if(btn)btn.disabled=false;});});}'
    'document.addEventListener("keydown",function(e){if(e.key==="Escape")close();});'
    "document.addEventListener('click',function(e){"
    'var a=e.target.closest&&e.target.closest("[data-wb-ctor-action]");'
    'if(!a)return;'
    'var act=a.getAttribute("data-wb-ctor-action");'
    'if(act==="form"){e.preventDefault();'
    'open(\'<h3 class="wb-ctor-modal-title">Оставить заявку</h3>\''
    '+ \'<form><input type="text" name="fb_name" placeholder="Ваше имя" required>\''
    '+ \'<input type="tel" name="fb_phone" placeholder="Телефон" required>\''
    '+ \'<textarea name="fb_message" placeholder="Сообщение (необязательно)"></textarea>\''
    '+ \'<button type="submit">Отправить</button>\''
    '+ \'<div class="wb-ctor-msg"></div></form>\');'
    'bindFeedback(document.querySelector(".wb-ctor-modal form"));}'
    'else if(act==="subscribe"){e.preventDefault();'
    'open(\'<h3 class="wb-ctor-modal-title">Подписка на рассылку</h3>\''
    '+ \'<form><input type="email" name="email" placeholder="Ваш e-mail" required>\''
    '+ \'<button type="submit">Подписаться</button>\''
    '+ \'<div class="wb-ctor-msg"></div></form>\');'
    'bindSubscribe(document.querySelector(".wb-ctor-modal form"));}'
    'else if(act==="popup"){e.preventDefault();'
    'var c=a.parentElement&&a.parentElement.querySelector(".wb-ctor-popup-content");'
    'if(c)open(c.innerHTML);}'
    '},false);'
    '})();</script>'
)


# Отзывы (цикл 4 / этап 6): кнопка «Оставить свой отзыв» (bitrix-jqm в клоне
# мёртвая — бесконечная загрузка) открывает рабочую форму: имя, оценка 1–5,
# товар (необязательно), текст → POST /api/review (на модерацию). Динамическая
# лента одобренных отзывов — .wb-reviews-dynamic (серверный рендер). Длинные
# тексты лент обрезаются с кнопкой «Отзыв полностью». Мобильные правила ≤640px.
REVIEW_CSS = (
    '<style>'
    '.wb-review-stars-input{display:flex;gap:4px;margin-bottom:10px;}'
    '.wb-review-stars-input input{position:absolute;opacity:0;pointer-events:none;}'
    '.wb-review-stars-input label{font-size:28px;color:#ccc;cursor:pointer;line-height:1;}'
    '.wb-review-stars-input input:checked ~ label,'
    '.wb-review-stars-input label:hover,'
    '.wb-review-stars-input label:hover ~ label{color:#e8b64c;}'
    '.wb-review-stars-input input:checked + label{color:#e8b64c;}'
    '.wb-reviews-dynamic{max-width:1344px;margin:0 auto;padding:16px 24px;box-sizing:border-box;}'
    '.wb-reviews-dynamic h2{font-size:1.5rem;margin:0 0 16px;}'
    '.wb-review-card{background:#fff;border:1px solid #eee;border-radius:10px;'
    'padding:18px 20px;margin-bottom:14px;}'
    '.wb-review-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:6px;}'
    '.wb-review-name{font-weight:600;font-size:1.05rem;}'
    '.wb-review-stars{color:#e8b64c;letter-spacing:2px;}'
    '.wb-review-date{color:#999;font-size:0.85rem;margin-left:auto;}'
    '.wb-review-product{color:#888;font-size:0.85rem;margin-bottom:4px;}'
    '.wb-review-text{color:#333;font-size:0.95rem;line-height:1.55;}'
    '.wb-review-text.clamped{display:-webkit-box;-webkit-line-clamp:6;'
    '-webkit-box-orient:vertical;overflow:hidden;}'
    '.wb-review-toggle{display:inline-block;margin-top:8px;border:0;background:none;'
    'color:#b49d84;cursor:pointer;font-size:0.9rem;text-decoration:underline;padding:0;}'
    # PG-6.1/6.4: контейнеры страниц «О компании»/«Отзывы»/«Документы»
    '#content .middle > .right_block > .middle > .container,'
    '#content .middle > .container{max-width:1344px;margin-left:auto;margin-right:auto;'
    'padding-left:24px;padding-right:24px;box-sizing:border-box;}'
    '#content .add_review{max-width:1344px;margin:0 auto;padding:16px 24px;box-sizing:border-box;}'
    '#content .front_review .maxwidth-theme{max-width:1344px;box-sizing:border-box;}'
    '#content .body-info{max-width:1344px;margin-left:auto;margin-right:auto;'
    'padding-left:24px;padding-right:24px;box-sizing:border-box;}'
    # PG-6.2: мобильная вёрстка ленты отзывов (ширина ≤ 640px)
    '@media (max-width:640px){'
    '#content{overflow-x:hidden;}'
    '#content .container,#content .add_review,#content .body-info,'
    '.wb-reviews-dynamic{padding-left:14px;padding-right:14px;}'
    '#content .front_review .item{padding:12px !important;}'
    '#content .front_review .item-wrapper{padding:0 !important;}'
    '#content .front_review .top-info{float:none !important;width:100% !important;'
    'margin:0 0 8px !important;}'
    '#content .front_review .text{margin-left:0 !important;}'
    '#content .front_review .text .wb-review-text,'
    '#content .front_review .text > div,'
    '#content .front_review .text{font-size:0.92rem !important;}'
    '.wb-review-card{padding:14px;}'
    '.wb-review-head{gap:6px;}'
    '.wb-review-date{margin-left:0;width:100%;}'
    '}'
    '</style>'
)

# JS формы отзыва — отдельный файл static/js/review-form.js (кавычки в
# разметке формы не проходят через Python-литералы надёжно); версия по mtime.
REVIEW_JS_SRC = '<script src="/static/js/review-form.js?v={v}" defer></script>'

CATALOG_LIVE_JS = (
    '<script>(function(){'
    'if(window.__wbCatalogLive)return;window.__wbCatalogLive=1;'
    'var timers=new WeakMap();'
    'function resultsBox(f){return f.parentNode?'
    'f.parentNode.querySelector(".wb-catalog-results"):null;}'
    'function formParams(f){'
    'var u=new URLSearchParams(),fd=new FormData(f);'
    'fd.forEach(function(v,key){if(v!=="")u.append(key,v);});'
    'u.delete("page");return u;}'
    'function load(u,box,push){'
    'if(!box)return;'
    'box.classList.add("wb-loading");'
    'fetch(u,{headers:{"X-Requested-With":"fetch"}})'
    '.then(function(r){return r.json();})'
    '.then(function(d){'
    'if(!d||!d.success)throw new Error("bad response");'
    'box.innerHTML=d.html;'
    'box.classList.remove("wb-loading");'
    'if(push&&window.self===window.top){'
    'try{history.pushState({wb:1},"",push);}catch(e){}}'
    'document.dispatchEvent(new CustomEvent("wb:results-updated"));'
    '})'
    '.catch(function(){box.classList.remove("wb-loading");});'
    '}'
    'function applyFilters(f){'
    'var box=resultsBox(f);if(!box)return;'
    'var u=formParams(f),qs=u.toString();'
    'var base=f.getAttribute("data-base")||box.getAttribute("data-base")||"/catalog/";'
    'var api="/api/catalog?"+qs+(qs?"&":"")+"base="+encodeURIComponent(base);'
    'load(api,box,base+(qs?"?"+qs:""));'
    '}'
    'function schedule(f,delay){'
    'clearTimeout(timers.get(f));'
    'timers.set(f,setTimeout(function(){applyFilters(f);},delay));'
    '}'
    'document.addEventListener("change",function(e){'
    'var f=e.target.closest&&e.target.closest("form.wb-catalog-filters");'
    'if(f)schedule(f,0);'
    '});'
    'document.addEventListener("input",function(e){'
    'var f=e.target.closest&&e.target.closest("form.wb-catalog-filters");'
    'if(!f)return;'
    'if(e.target.tagName==="SELECT"||e.target.type==="checkbox")return;'
    'schedule(f,400);'
    '});'
    'document.addEventListener("click",function(e){'
    'if(e.shiftKey||e.metaKey||e.ctrlKey||e.altKey)return;'
    'var a=e.target.closest&&'
    'e.target.closest(".wb-catalog-results .wb-catalog-pagination a");'
    'if(!a)return;'
    'var box=a.closest(".wb-catalog-results");'
    'if(!box)return;'
    'e.preventDefault();e.stopPropagation();'
    'var href=a.getAttribute("href")||"";'
    'var q=href.split("?")[1]||"";'
    'var base=box.getAttribute("data-base")||"/catalog/";'
    'load("/api/catalog?"+q+(q?"&":"")+"base="+encodeURIComponent(base),box,href);'
    'var y=box.getBoundingClientRect().top+window.pageYOffset-80;'
    'window.scrollTo(0,y<0?0:y);'
    '},true);'
    'window.addEventListener("popstate",function(e){'
    'if(e.state&&e.state.wb)location.reload();'
    '});'
    '})();</script>'
)


def _inject_lazyload_fix(html):
    """Инжект полифилла Bitrix-lazyload (static/js/lazyload-fix.js) перед </body>.

    На клонированных страницах фоны/картинки используют .lazy[data-bg]/[data-src],
    а плагин ls.unveilhooks загружается раньше lazysizes и не регистрируется —
    картинки не подгружались, оставался спиннер и пустые колонки.
    Скрипт вставляется всем посетителям (проблема у всех, не только у админа).

    Дополнительно в том же блоке:
    — инлайн-фикс мёртвой bitrix-пагинации категорий: клик по «Показать ещё»
      (.ajax_load_btn) и по номерам страниц (.module-pagination, ?PAGEN_1=N)
      перенаправляет на единый каталог в режиме «все товары» (per_page=all);
      перехват в capture-фазе, чтобы jQuery-делегированный обработчик aspro
      (bubble) не успел отработать;
    — состояние избранного посетителя (window.__likedIds/__likesCount) для
      восстановления кнопок и счётчика после перезагрузки;
    — переопределение ховер-блока карточек (см. _HOVER_CSS);
    — мёртвый bitrix-фильтр категорий (form.smartfilter): «Показать»/submit/
      сортировка перенаправляют в единый каталог с рабочими параметрами
      (price_from/price_to/sort, category раздела из B), а не в ajax-заглушку
      ?ajax=y, отвечающую json_failure;
    — левое меню категорий (.left_block): список свёрнут по умолчанию,
      раскрытие по клику по шапке (.opened) или наведению, приклеивание
      sticky-sidebar отключено — меню не перекрывает контент.
    — рерайт мёртвых битрикс-ссылок карточек + быстрая карточка-модалка
      (см. _rewrite_legacy_product_links / LEGACY_MODAL_JS).
    """
    if 'lazyload-fix.js' in html or '</body>' not in html:
        return html
    html = _rewrite_legacy_product_links(html)
    fix_ver = int(os.path.getmtime(os.path.join(BASE_DIR, 'static', 'js', 'lazyload-fix.js')))
    review_ver = int(os.path.getmtime(os.path.join(BASE_DIR, 'static', 'js', 'review-form.js')))
    base = json.dumps(_section_catalog_base())
    liked_ids = _visitor_liked_ids()
    liked = json.dumps(liked_ids)
    inject = (f'<script src="/static/js/lazyload-fix.js?v={fix_ver}" defer></script>\n'
              # Избранное посетителя: wb-actions.js размечает кнопки и счётчик.
              f'<script>window.__likedIds={liked};'
              f'window.__likesCount={len(liked_ids)};</script>\n'
              + _HOVER_CSS + '\n'
              + LIKED_BTN_CSS + '\n'
              + LEFTMENU_CSS + '\n'
              + CATALOG_LAYOUT_CSS + '\n'
              + SECTION_FILTER_CSS + '\n'
              + (SECTION_LISTING_HIDE_CSS if re.match(
                  r'^/catalog/(?:namatrasniki|odeyala|naperniki)/$',
                  request.path or '') else '') + '\n'
              + (SECTION_LAYOUT_CSS if re.match(
                  r'^/catalog/(?:namatrasniki|odeyala|naperniki)/$',
                  request.path or '') else '') + '\n'
              + SOCIALS_CSS + '\n'
              + LEGACY_MODAL_CSS + '\n'
              + LEGACY_MODAL_JS + '\n'
              + CATALOG_LIVE_CSS + '\n'
              + CATALOG_LIVE_JS + '\n'
              + SUBSCRIBE_JS + '\n'
              + CTOR_ACTION_CSS + '\n'
              + CTOR_ACTION_JS + '\n'
              + REVIEW_CSS + '\n'
              + REVIEW_JS_SRC.format(v=review_ver) + '\n'
              # Мёртвая пагинация категорий: «Показать ещё» → все товары раздела
              # одним списком, номера страниц → тот же список (?page=N игнорируется
              # в режиме per_page=all).
              '<script>(function(){'
              f'var B={base};'
              'function go(u){location.href=u;}'
              "document.addEventListener('click',function(e){"
              "var b=e.target.closest&&e.target.closest('.ajax_load_btn');"
              'if(b){e.preventDefault();e.stopPropagation();go(B);return;}'
              'var a=e.target.closest&&e.target.closest(\'.module-pagination a[href*="PAGEN_"]\');'
              'if(a){e.preventDefault();e.stopPropagation();'
              "var m=(a.getAttribute('href')||'').match(/PAGEN_\\d+=(\\d+)/);"
              "go(B+(B.indexOf('?')>-1?'&':'?')+'page='+(m?m[1]:1));}"
              '},true);'
              # Мёртвый bitrix-фильтр категорий (form.smartfilter): «Показать»
              # уходит в ajax-заглушку (?ajax=y) и json_failure — фильтр как будто
              # сбрасывается. Перенаправляем в единый каталог с рабочими
              # параметрами: цена из полей фильтра, сортировка из sort_btn,
              # размеры (группы sizes/razmernamatrasnika/razmerpostelnogobelya/
              # board_height) → size, цвет (radio, группа color) → color.
              'function sfVals(f,cb){'
              'var g=cb.closest("[data-prop_code]");if(!g)return null;'
              'var code=g.getAttribute("data-prop_code");'
              'var lab=f.querySelector("label[for=\\""+cb.id+"\\"]");'
              'var t=lab?lab.textContent:"";'
              't=t.replace(/\\s*\\(\\s*\\d+\\s*\\)\\s*$/,"").trim();'
              'return t?{code:code,v:t}:null;}'
              'function sfUrl(sort){'
              'var u=new URL(B,location.origin);'
              "var f=document.querySelector('form.smartfilter');"
              'if(f){'
              "var mn=f.querySelector('#MAX_SMART_FILTER_P1_MIN');"
              "var mx=f.querySelector('#MAX_SMART_FILTER_P1_MAX');"
              'if(mn&&mn.value)u.searchParams.set(\'price_from\',mn.value.replace(/\\D/g,\'\'));'
              'if(mx&&mx.value)u.searchParams.set(\'price_to\',mx.value.replace(/\\D/g,\'\'));'
              'var sizes=[],color="";'
              'f.querySelectorAll("input[type=checkbox]:checked").forEach(function(cb){'
              'var x=sfVals(f,cb);if(!x)return;'
              'if(x.code==="color"){color=x.v;}'
              'else{sizes.push(x.v);}});'
              'f.querySelectorAll("input[type=radio]:checked").forEach(function(r){'
              'var x=sfVals(f,r);if(x&&x.code==="color"&&!color)color=x.v;});'
              'if(sizes.length)u.searchParams.set("size",sizes.join(","));'
              'if(color)u.searchParams.set("color",color);'
              '}'
              'if(sort)u.searchParams.set(\'sort\',sort);'
              'return u.pathname+u.search;'
              '}'
              "document.addEventListener('click',function(e){"
              "var p=e.target.closest&&e.target.closest("
              "'#set_filter1,.bx_filter_search_button,.bx_filter_popup_result a');"
              'if(p&&p.closest&&p.closest(\'form.smartfilter\')){'
              'e.preventDefault();e.stopPropagation();go(sfUrl(\'\'));return;}'
              "var s=e.target.closest&&e.target.closest('a.sort_btn');"
              'if(s){e.preventDefault();e.stopPropagation();'
              "var c=s.className;var sort='popular';"
              "if(c.indexOf('PRICE')>-1){sort=c.indexOf('desc')>-1?'price_desc':'price_asc';}"
              "go(sfUrl(sort));}"
              '},true);'
              "document.addEventListener('submit',function(e){"
              "if(e.target&&e.target.classList&&e.target.classList.contains('smartfilter')){"
              'e.preventDefault();go(sfUrl(\'\'));}'
              '},true);'
              # Левое меню категорий: свёрнуто по умолчанию (LEFTMENU_CSS),
              # клик по шапке «Каталог» переключает .opened — раскрытие без
              # наезда на контент; обработчик aspro на шапке не нужен.
              "document.addEventListener('click',function(e){"
              "var h=e.target.closest&&e.target.closest('.left_block .slide-block__head');"
              'if(h){var m=h.closest(\'.menu_top_block\');'
              'if(m){e.preventDefault();e.stopPropagation();m.classList.toggle(\'opened\');}}'
              '},true);'
              "document.addEventListener('DOMContentLoaded',function(){"
              "document.querySelectorAll('.ajax_load_btn').forEach(function(btn){"
              'if(btn.tagName==="A")return;'
              "var a=document.createElement('a');a.href=B;a.className=btn.className;"
              "a.setAttribute('style','display:block;text-align:center;text-decoration:none;color:inherit;');"
              'a.innerHTML=btn.innerHTML;btn.parentNode.replaceChild(a,btn);});'
              '});})();</script>\n'
              + CART_INTERCEPT_JS + '\n'
              '</body>')
    return html.replace('</body>', inject, 1)


def _inject_admin_edit_button(html, edit_url):
    """Плавающая кнопка «Редактировать страницу» для залогиненного админа.

    Вставляется только когда админ авторизован (сессионная кука), поэтому
    обычные посетители получают исходный HTML. HTML-страницы сайта отдаются
    с Cache-Control: no-store (add_cache_headers), так что закэшированной
    «без-кнопочной» версии админу не достанется.
    """
    if not session.get('admin_user'):
        return html
    if not _role_allows(_admin_role(), 'pages'):
        return html
    # Режим редактора-обёртки: страница грузится внутрь iframe (?pe_bare=1) —
    # плавающая кнопка редактирования внутри контента не нужна.
    if request.args.get('pe_bare'):
        return html
    button = (
        '<style>'
        '#admin-edit-fab{position:fixed;right:18px;bottom:18px;z-index:99999;'
        'display:inline-flex;align-items:center;background:#b49d84;color:#fff;'
        'padding:0.65rem 1.15rem;border-radius:24px;font:600 14px/1 sans-serif;'
        'text-decoration:none;box-shadow:0 4px 14px rgba(0,0,0,.25);}'
        '#admin-edit-fab:hover{background:#9a8269;}'
        '</style>'
        f'<a id="admin-edit-fab" href="{edit_url}">✏ Редактировать страницу</a>'
    )
    if '</body>' in html:
        return html.replace('</body>', button + '\n</body>', 1)
    return html + button


def _pe_bare_admin():
    """Запрос контента для редактора-обёртки (?pe_bare=1) от авторизованного
    админа с правом редактирования страниц."""
    return (request.args.get('pe_bare')
            and session.get('admin_user')
            and _role_allows(_admin_role(), 'pages'))


def _render_page_html_marked(content, page, extra_ctx=None):
    """HTML страницы с маркерами блоков <!--blk:N--> (рендер для редактора)."""
    blocks = page.get('blocks')
    if not isinstance(blocks, list):
        blocks = [{'type': 'html', 'legacy': True, 'html': page.get('content_html', '')}]
    edit_page = dict(page)
    edit_page['content_html'] = blocks_module.render_blocks_marked(blocks)
    ctx = make_context(content, edit_page)
    if extra_ctx:
        ctx.update(extra_ctx)
    return render_template(page['template'], **ctx)


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
    if slug not in SYSTEM_PAGE_SLUGS:
        # Обычные страницы: редактор-обёртка (page_editor_wrapper.html) — контент
        # страницы грузится в same-origin iframe шириной 1200/768/360, поэтому
        # media queries страницы применяются честно (режимы Tablet/Mobile).
        editor_ver = int(os.path.getmtime(os.path.join(BASE_DIR, 'static', 'js', 'page-editor.js')))
        html = render_template('page_editor_wrapper.html', slug=slug,
                               content_url=page.get('url') or '/',
                               csrf_token=get_csrf_token(), editor_ver=editor_ver)
        return Response(html)
    html = _render_page_html_marked(content, page)
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
    # Чистим и БД-запись страницы: иначе recreate того же slug подхватывает
    # старые draft_blocks/published_blocks и версии (приёмка цикла 2).
    from models import Page, PageVersion
    try:
        db.session.query(PageVersion).filter_by(page_slug=slug).delete()
        db.session.query(Page).filter_by(slug=slug).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
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


SECTION_PRESETS_FILE = BASE_DIR / 'data' / 'section_presets.json'


@app.route('/admin/api/section-presets')
@admin_required
def admin_section_presets():
    """Пресеты секций дизайна для библиотеки конструктора (data/section_presets.json)."""
    try:
        presets = json.loads(SECTION_PRESETS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        presets = []
    if not isinstance(presets, list):
        presets = []
    return jsonify({'success': True, 'presets': presets})


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


@app.route('/admin/api/theme-preview', methods=['POST'])
@admin_required
def admin_theme_preview():
    """Живой предпросмотр темы (CN-T7.4): CSS из переданной темы БЕЗ сохранения."""
    payload = request.get_json() or {}
    theme = _validated_theme(payload.get('theme'))
    if theme is None:
        return jsonify({'success': False, 'error': 'Некорректная тема'}), 400
    return jsonify({'success': True, 'css': _theme_css_text(theme)})


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


def _product_section_response(data):
    """Назначение товару раздела сайта (catalog__* slug) или '' (все разделы)."""
    from db import db as _db
    from models import Product

    def _set_one(source, product_id, section):
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return False
        prod = _db.session.query(Product).filter_by(source=source, nm_id=product_id).first()
        if prod is None:
            return False
        prod.site_section = (section or '')[:60]
        return True

    section = data.get('section', '')
    items = data.get('items')
    if items:  # массовое назначение
        count = 0
        for it in items:
            if _set_one(it.get('source', 'wb'),
                        it.get('product_id') or it.get('id'), section):
                count += 1
        _db.session.commit()
        invalidate_site_cache()
        return jsonify({'success': True, 'count': count})
    product_id = data.get('product_id') or data.get('nm_id')
    if not product_id:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    if not _set_one(data.get('source', 'wb'), product_id, section):
        return jsonify({'success': False, 'error': 'Товар не найден'}), 404
    _db.session.commit()
    invalidate_site_cache()
    return jsonify({'success': True})


@app.route('/admin/api/product-section', methods=['POST'])
@admin_required
def admin_product_section():
    """Раздел сайта для товара: один ({source, product_id, section}) или массово ({items, section})."""
    return _product_section_response(request.get_json() or {})


@app.route('/admin/api/site-sections')
@admin_required
def admin_site_sections():
    """Разделы каталога сайта — для выпадающих списков в админке."""
    content = load_content()
    sections = []
    for p in content.get('pages', []):
        m = re.match(r'^catalog__([a-z0-9]+)$', p.get('slug') or '')
        if m:
            title = (p.get('h1') or '').strip() or p.get('title') or m.group(1)
            sections.append({'key': m.group(1), 'title': title})
    return jsonify({'success': True, 'sections': sections})


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


@app.route('/admin/api/product-update', methods=['POST'])
@admin_required
def admin_product_update():
    """Полное редактирование товара (замечание «управление товарами»):
    название, описание, базовая цена, скидка для сайта, фото (URL),
    категория, видимость, бренд, артикул, ссылка, рейтинг, остаток,
    раздел сайта, характеристики, SEO title/description, галерея изображений,
    текст бейджа наличия, явные группы вариантов (чипы). Скидка задаётся
    теми же полями admin_discount_*, что и /admin/api/product-discount.
    Поля, которые перезаписывает синхронизация WB (название, бренд, артикул,
    цена, фото, ссылка, остаток, видимость), автоматически закрепляются в
    manual_overrides — синк их больше не трёт (снять закрепление:
    /admin/api/product-unpin). Изменения сразу видны на сайте —
    invalidate_site_cache() сбрасывает кэш каталога и страниц.
    """
    from db import db as _db
    from models import Product as _Product

    data = request.get_json() or {}
    source = data.get('source', 'wb')
    pid = data.get('product_id') or data.get('nm_id')
    if not pid:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    prod = _db.session.query(_Product).filter_by(
        source=source, nm_id=int(pid)).first()
    if prod is None:
        return jsonify({'success': False, 'error': 'Товар не найден'}), 404

    changes = {}
    pinned = set()

    def _pin_if_changed(field, old, new):
        # закрепляем только реально изменённые поля: форма шлёт все поля
        # на каждое сохранение, иначе любое правило закрепило бы всё сразу
        if old != new:
            pinned.add(field)

    if 'name' in data:
        new_val = str(data.get('name') or '').strip()[:1000]
        _pin_if_changed('name', prod.name or '', new_val)
        prod.name = new_val
        changes['name'] = prod.name
    if 'description' in data:
        # описание храним plaintext (без HTML) — в extra['description']
        desc = re.sub(r'<[^>]+>', '', str(data.get('description') or ''))[:5000]
        extra = dict(prod.extra) if isinstance(prod.extra, dict) else {}
        if desc:
            extra['description'] = desc.strip()
        else:
            extra.pop('description', None)
        prod.extra = extra
        changes['description'] = desc.strip()
    if 'price' in data:
        try:
            new_val = max(0, min(10**7, int(float(data.get('price') or 0))))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'price must be a number'}), 400
        _pin_if_changed('price', prod.price or 0, new_val)
        prod.price = new_val
        changes['price'] = prod.price
    if 'discount_type' in data or 'discount_value' in data:
        dtype = data.get('discount_type', prod.admin_discount_type or 'percent')
        if dtype not in ('percent', 'amount'):
            return jsonify({'success': False, 'error': 'discount_type must be percent|amount'}), 400
        try:
            val = int(float(data.get('discount_value', 0) or 0))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'discount_value must be a number'}), 400
        if dtype == 'amount':
            prod.admin_discount_type, prod.admin_discount_amount = 'amount', max(0, val)
            prod.admin_discount_percent = 0
        else:
            prod.admin_discount_type, prod.admin_discount_percent = 'percent', max(0, min(99, val))
            prod.admin_discount_amount = 0
        changes['discount'] = f'{dtype}:{val}'
    if 'photo' in data:
        photo = str(data.get('photo') or '').strip()[:2000]
        if photo and not (photo.startswith(('http://', 'https://', '/static/', '/proxy/', '/upload/'))):
            return jsonify({'success': False, 'error': 'photo must be an URL or /static/... path'}), 400
        _pin_if_changed('photo', prod.photo or '', photo)
        prod.photo = photo
        changes['photo'] = photo[:120]
    if 'category' in data:
        prod.category = str(data.get('category') or '').strip()[:300]
        changes['category'] = prod.category
    if 'stock' in data:
        try:
            new_val = max(0, int(float(data.get('stock') or 0)))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'stock must be a number'}), 400
        _pin_if_changed('stock', prod.stock or 0, new_val)
        prod.stock = new_val
        changes['stock'] = prod.stock
    if 'visible' in data:
        new_val = bool(data.get('visible'))
        _pin_if_changed('visible', bool(prod.visible), new_val)
        prod.visible = new_val
        changes['visible'] = prod.visible
    if 'brand' in data:
        new_val = str(data.get('brand') or '').strip()[:300]
        _pin_if_changed('brand', prod.brand or '', new_val)
        prod.brand = new_val
        changes['brand'] = prod.brand
    if 'vendor_code' in data:
        new_val = str(data.get('vendor_code') or '').strip()[:300]
        _pin_if_changed('vendor_code', prod.vendor_code or '', new_val)
        prod.vendor_code = new_val
        changes['vendor_code'] = prod.vendor_code
    if 'url' in data:
        new_val = str(data.get('url') or '').strip()[:2000]
        _pin_if_changed('url', prod.url or '', new_val)
        prod.url = new_val
        changes['url'] = prod.url[:120]
    if 'rating' in data:
        try:
            rating = data.get('rating')
            prod.rating = None if rating in (None, '') else max(0.0, min(5.0, float(rating)))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'rating must be a number'}), 400
        changes['rating'] = prod.rating
    if 'review_count' in data:
        try:
            prod.review_count = max(0, int(float(data.get('review_count') or 0)))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'review_count must be a number'}), 400
        changes['review_count'] = prod.review_count
    if 'site_section' in data:
        prod.site_section = str(data.get('site_section') or '').strip()[:60]
        changes['site_section'] = prod.site_section
    if 'characteristics' in data or 'seo_title' in data or 'seo_description' in data \
            or 'gallery' in data or 'stock_label' in data or 'variants' in data \
            or 'composition' in data or 'care' in data or 'size_chart' in data \
            or 'video_url' in data or 'badges' in data:
        # характеристики, SEO, галерея, бейдж наличия и варианты — в extra
        extra = dict(prod.extra) if isinstance(prod.extra, dict) else {}
        if 'characteristics' in data:
            chars = data.get('characteristics') or {}
            if not isinstance(chars, dict):
                return jsonify({'success': False, 'error': 'characteristics must be an object'}), 400
            for k in list(extra.keys()):
                if k not in ('description', 'seo_title', 'seo_description',
                             'gallery', 'stock_label', 'variants',
                             'composition', 'care', 'size_chart', 'video_url',
                             'badges'):
                    extra.pop(k)
            for k, v in chars.items():
                key = str(k).strip()[:200]
                if key:
                    extra[key] = str(v)[:2000]
            changes['characteristics'] = len(chars)
        for seo_key in ('seo_title', 'seo_description'):
            if seo_key in data:
                value = re.sub(r'<[^>]+>', '', str(data.get(seo_key) or '')).strip()[:500]
                if value:
                    extra[seo_key] = value
                else:
                    extra.pop(seo_key, None)
                changes[seo_key] = value[:120]
        if 'gallery' in data:
            gallery = data.get('gallery') or []
            if not isinstance(gallery, list):
                return jsonify({'success': False, 'error': 'gallery must be a list'}), 400
            urls = [str(g).strip()[:2000] for g in gallery if str(g or '').strip()]
            bad = [g for g in urls if not g.startswith(('http://', 'https://', '/static/', '/proxy/', '/upload/'))]
            if bad:
                return jsonify({'success': False, 'error': 'gallery items must be URLs or /static/... paths'}), 400
            if urls:
                extra['gallery'] = urls
            else:
                extra.pop('gallery', None)
            changes['gallery'] = len(urls)
        if 'stock_label' in data:
            # свой текст бейджа наличия; пусто — авто («Много»/«Осталось мало»/…)
            value = re.sub(r'<[^>]+>', '', str(data.get('stock_label') or '')).strip()[:100]
            if value:
                extra['stock_label'] = value
            else:
                extra.pop('stock_label', None)
            changes['stock_label'] = value
        if 'variants' in data:
            variants = data.get('variants') or {}
            if not isinstance(variants, dict):
                return jsonify({'success': False, 'error': 'variants must be an object'}), 400
            clean = {}
            for group, values in variants.items():
                key = str(group).strip()[:60]
                if key not in ('colors', 'heights', 'sizes'):
                    continue  # чипы на карточке строятся только для этих групп
                if not isinstance(values, list):
                    return jsonify({'success': False, 'error': 'variants values must be lists'}), 400
                vals = [str(v).strip()[:100] for v in values if str(v or '').strip()]
                if vals:
                    clean[key] = vals
            if clean:
                extra['variants'] = clean
            else:
                extra.pop('variants', None)
            changes['variants'] = sorted(clean)
        # состав, уход, размерная сетка, видео и бейджи («Хит»/«Новинка») —
        # админские правки поверх данных API; пустое значение = удаление
        # правки (возврат к дефолтному отсутствию блока на карточке)
        for text_key, maxlen in (('composition', 3000), ('care', 3000),
                                 ('size_chart', 5000), ('video_url', 500)):
            if text_key in data:
                if text_key == 'video_url':
                    value = str(data.get(text_key) or '').strip()[:maxlen]
                    if value and not value.startswith(('http://', 'https://', '/')):
                        return jsonify({'success': False, 'error': 'video_url must be an URL'}), 400
                else:
                    value = str(data.get(text_key) or '').strip()[:maxlen]
                if value:
                    extra[text_key] = value
                else:
                    extra.pop(text_key, None)
                changes[text_key] = value[:120]
        if 'badges' in data:
            badges = data.get('badges') or []
            if not isinstance(badges, list):
                return jsonify({'success': False, 'error': 'badges must be a list'}), 400
            allowed = ('hit', 'new')
            clean_badges = [str(b).strip() for b in badges if str(b).strip() in allowed]
            if clean_badges:
                extra['badges'] = clean_badges
            else:
                extra.pop('badges', None)
            changes['badges'] = clean_badges
        prod.extra = extra

    if pinned:
        current = set(prod.manual_overrides or []) if isinstance(prod.manual_overrides, list) else set()
        prod.manual_overrides = sorted(current | pinned)

    _db.session.commit()
    _audit_log('product.update', 'product', f'{source}:{pid}', changes)
    invalidate_site_cache()
    return jsonify({'success': True,
                    'manual_overrides': prod.manual_overrides or []})


@app.route('/admin/api/product-create', methods=['POST'])
@admin_required
def admin_product_create():
    """Создание товара в админке (замечание «свои товары»): товар сайта
    без привязки к WB/Ozon. Появляется в общем каталоге и получает свою
    страницу /catalog/site/<id>/. nm_id приравниваем id — чтобы схема
    url товара и редактирование (product-update по source+nm_id) были
    единообразными для всех источников.
    """
    from db import db as _db
    from models import Product as _Product

    data = request.get_json() or {}
    name = str(data.get('name') or '').strip()[:1000]
    if not name:
        return jsonify({'success': False, 'error': 'name required'}), 400
    price = data.get('price', 0)
    try:
        price = max(0, min(10**7, int(float(price or 0))))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'price must be a number'}), 400
    dtype = data.get('discount_type', 'percent')
    if dtype not in ('percent', 'amount'):
        return jsonify({'success': False, 'error': 'discount_type must be percent|amount'}), 400
    try:
        dval = int(float(data.get('discount_value', 0) or 0))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'discount_value must be a number'}), 400
    photo = str(data.get('photo') or '').strip()[:2000]
    if photo and not (photo.startswith(('http://', 'https://', '/static/', '/proxy/', '/upload/'))):
        return jsonify({'success': False, 'error': 'photo must be an URL or /static/... path'}), 400
    try:
        stock = max(0, int(float(data.get('stock', 0) or 0)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'stock must be a number'}), 400

    desc = re.sub(r'<[^>]+>', '', str(data.get('description') or ''))[:5000].strip()
    extra = {'description': desc} if desc else {}
    # характеристики (ключ-значение) и SEO — в extra рядом с описанием
    chars = data.get('characteristics') or {}
    if not isinstance(chars, dict):
        return jsonify({'success': False, 'error': 'characteristics must be an object'}), 400
    for k, v in chars.items():
        key = str(k).strip()[:200]
        if key:
            extra[key] = str(v)[:2000]
    for seo_key in ('seo_title', 'seo_description'):
        value = re.sub(r'<[^>]+>', '', str(data.get(seo_key) or '')).strip()[:500]
        if value:
            extra[seo_key] = value
    # галерея, бейдж наличия и явные варианты — как в product-update
    gallery = data.get('gallery') or []
    if not isinstance(gallery, list):
        return jsonify({'success': False, 'error': 'gallery must be a list'}), 400
    urls = [str(g).strip()[:2000] for g in gallery if str(g or '').strip()]
    bad = [g for g in urls if not g.startswith(('http://', 'https://', '/static/', '/proxy/', '/upload/'))]
    if bad:
        return jsonify({'success': False, 'error': 'gallery items must be URLs or /static/... paths'}), 400
    if urls:
        extra['gallery'] = urls
    stock_label = re.sub(r'<[^>]+>', '', str(data.get('stock_label') or '')).strip()[:100]
    if stock_label:
        extra['stock_label'] = stock_label
    variants = data.get('variants') or {}
    if not isinstance(variants, dict):
        return jsonify({'success': False, 'error': 'variants must be an object'}), 400
    clean = {}
    for group, values in variants.items():
        key = str(group).strip()[:60]
        if key not in ('colors', 'heights', 'sizes') or not isinstance(values, list):
            continue
        vals = [str(v).strip()[:100] for v in values if str(v or '').strip()]
        if vals:
            clean[key] = vals
    if clean:
        extra['variants'] = clean
    # состав, уход, размерная сетка, видео и бейджи — как в product-update
    for text_key, maxlen in (('composition', 3000), ('care', 3000),
                             ('size_chart', 5000)):
        value = str(data.get(text_key) or '').strip()[:maxlen]
        if value:
            extra[text_key] = value
    video_url = str(data.get('video_url') or '').strip()[:500]
    if video_url:
        if not video_url.startswith(('http://', 'https://', '/')):
            return jsonify({'success': False, 'error': 'video_url must be an URL'}), 400
        extra['video_url'] = video_url
    badges = data.get('badges') or []
    if not isinstance(badges, list):
        return jsonify({'success': False, 'error': 'badges must be a list'}), 400
    clean_badges = [str(b).strip() for b in badges if str(b).strip() in ('hit', 'new')]
    if clean_badges:
        extra['badges'] = clean_badges
    rating = None
    if data.get('rating') not in (None, ''):
        try:
            rating = max(0.0, min(5.0, float(data.get('rating'))))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'rating must be a number'}), 400
    try:
        review_count = max(0, int(float(data.get('review_count', 0) or 0)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'review_count must be a number'}), 400

    prod = _Product(source='site', nm_id=0, name=name,
                    brand=str(data.get('brand') or 'Экотория')[:300],
                    vendor_code=str(data.get('vendor_code') or '').strip()[:300],
                    category=str(data.get('category') or '').strip()[:300],
                    price=price, photo=photo,
                    url=str(data.get('url') or '').strip()[:2000],
                    stock=stock, rating=rating, review_count=review_count,
                    admin_discount_type=dtype,
                    admin_discount_percent=max(0, min(99, dval)) if dtype == 'percent' else 0,
                    admin_discount_amount=max(0, dval) if dtype == 'amount' else 0,
                    extra=extra or None,
                    site_section=str(data.get('site_section') or '').strip()[:60],
                    visible=bool(data.get('visible', True)))
    _db.session.add(prod)
    _db.session.flush()
    prod.nm_id = prod.id  # уникален в рамках source='site'
    _db.session.commit()
    _audit_log('product.create', 'product', f'site:{prod.id}',
               {'name': name, 'price': price})
    invalidate_site_cache()
    return jsonify({'success': True, 'product_id': prod.id,
                    'url': f'/catalog/site/{prod.id}/'})


@app.route('/admin/api/product-delete', methods=['POST'])
@admin_required
def admin_product_delete():
    """Удаление товара (в первую очередь созданных вручную, source='site';
    для WB/Ozon товаров удаление тоже допустимо — при следующей синхронизации
    они вернутся из API маркетплейса)."""
    from db import db as _db
    from models import Product as _Product

    data = request.get_json() or {}
    source = data.get('source', 'site')
    if source not in ('site', 'wb', 'ozon'):
        return jsonify({'success': False, 'error': 'bad source'}), 400
    pid = data.get('product_id') or data.get('nm_id')
    if not pid:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    prod = _db.session.query(_Product).filter_by(
        source=source, nm_id=int(pid)).first()
    if prod is None:
        return jsonify({'success': False, 'error': 'Товар не найден'}), 404
    _audit_log('product.delete', 'product', f'{source}:{pid}',
               {'name': prod.name})
    _db.session.delete(prod)
    _db.session.commit()
    invalidate_site_cache()
    return jsonify({'success': True})


@app.route('/admin/api/product-unpin', methods=['POST'])
@admin_required
def admin_product_unpin():
    """Снять закрепление полей товара: без fields снимается всё.
    После снятия следующая синхронизация WB снова обновит эти поля."""
    from db import db as _db
    from models import Product as _Product

    data = request.get_json() or {}
    source = data.get('source', 'wb')
    pid = data.get('product_id') or data.get('nm_id')
    if not pid:
        return jsonify({'success': False, 'error': 'product_id required'}), 400
    prod = _db.session.query(_Product).filter_by(
        source=source, nm_id=int(pid)).first()
    if prod is None:
        return jsonify({'success': False, 'error': 'Товар не найден'}), 404
    current = list(prod.manual_overrides or []) if isinstance(prod.manual_overrides, list) else []
    fields = data.get('fields')
    if fields:
        remove = {str(f) for f in fields}
        prod.manual_overrides = [f for f in current if f not in remove] or None
    else:
        prod.manual_overrides = None
    _db.session.commit()
    _audit_log('product.unpin', 'product', f'{source}:{pid}',
               {'fields': fields or 'all'})
    invalidate_site_cache()
    return jsonify({'success': True, 'manual_overrides': prod.manual_overrides or []})


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
            product_id = it.get('product_id') or it.get('id')
            try:
                product_id = int(product_id)
            except (TypeError, ValueError):
                continue
            if _set_product_discount(it.get('source', 'wb'), product_id,
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
    if _pe_bare_admin():
        # Контент для iframe редактора: с маркерами блоков, без кнопки админа.
        return Response(_inject_lazyload_fix(
            _render_page_html_marked(content, page, {'catalog_view': ctx['catalog_view']})))
    return Response(_inject_lazyload_fix(render_template(page['template'], **ctx)))


@app.route('/api/catalog')
def api_catalog():
    """Частичный HTML результатов каталога (сетка + счётчик + пагинация).

    Живые фильтры и ajax-пагинация блока маркетплейс запрашивают этот
    endpoint и подменяют контейнер .wb-catalog-results без перезагрузки.
    Параметры — те же, что у /catalog/ (q, category, size, color, цена,
    sort, page), плюс section (предфильтр раздела) и base (URL страницы,
    для ссылок пагинации и history.pushState).
    """
    section = (request.args.get('section') or '').strip()
    base = (request.args.get('base') or '').strip()
    if not base.startswith('/') or base.startswith('//'):
        base = ''
    if not base:
        base = ('/catalog/' + section + '/') if section else '/catalog/'
    view = _filtered_catalog(request.args, section=section, base=base,
                             show_filters=True)
    hide_mp = bool(load_content().get('site', {}).get('hide_marketplaces'))
    html = render_template('wb_catalog_results.html', catalog_view=view,
                           hide_mp=hide_mp)
    return jsonify({'success': True, 'html': html, 'total': view['total'],
                    'page': view['page'], 'pages': view['pages']})


def _filtered_catalog(args, section='', per_page=None, base='/catalog/',
                      show_filters=True):
    """Фильтрация/сортировка/пагинация каталога (CT-1, CT-5).

    Фильтры: q, source, category, in_stock, price_from/to, sort, size, color.
    size — размер из имени товара (нормализация x/х/*); значение вида
    «200x200» ищется подстрокой в нормализованном имени, значение-диапазон
    («17-20», «Высота борта, см») — по вхождению любого числа имени в
    диапазон. Несколько значений (повторные/через запятую) — логика ИЛИ.
    color — подстрока в имени (цвета WB/Ozon живут в названии карточки).

    section — предфильтр раздела каталога (напр. 'namatrasniki'): товары без
    назначенного раздела + товары этого раздела (правило совпадает с
    wb_products в make_context). per_page — переопределение размера страницы
    (главная показывает по 12). base/show_filters/section попадают в
    представление для шаблона (пагинация, data-атрибуты живого фильтра).
    """
    import math as _math
    import re as _re
    products = list(get_products())
    if section:
        products = [p for p in products
                    if not p.get('site_section') or p.get('site_section') == section]

    def _arg_list(key):
        if hasattr(args, 'getlist'):
            vals = args.getlist(key)
        else:
            vals = [args.get(key)] if args.get(key) else []
        out = []
        for v in vals:
            out.extend(str(v or '').split(','))
        return [v.strip() for v in out if v and v.strip()]

    q = (args.get('q') or '').strip().lower()
    if q:
        products = [p for p in products if q in (p.get('name') or '').lower()
                    or q in (p.get('brand') or '').lower()
                    or q in str(p.get('vendor_code') or '').lower()
                    or q in str(p.get('nm_id') or '')]
    source = args.get('source') or ''
    if source in ('wb', 'ozon', 'site'):
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

    def _norm_text(s):
        s = str(s or '').lower()
        for ch in ('х', '*', '×', 'х'):
            s = s.replace(ch, 'x')
        return s.replace('см', '').replace(' ', '')

    def _size_match(name_norm, wanted):
        w = _norm_text(wanted)
        if not w:
            return False
        m = _re.fullmatch(r'(\d+)-(\d+)', w)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            nums = [int(n) for n in _re.findall(r'\d+', name_norm)]
            return any(lo <= n <= hi for n in nums)
        if any(c.isdigit() for c in w):
            return w in name_norm
        # слово без цифр («Полуторный», «Евро») — корень первых 6 букв
        return w[:6] in name_norm

    sizes = _arg_list('size')
    if sizes:
        products = [p for p in products
                    if any(_size_match(_norm_text(p.get('name')), w) for w in sizes)]
    colors = [c.lower() for c in _arg_list('color')]
    if colors:
        products = [p for p in products
                    if any(c in (p.get('name') or '').lower() for c in colors)]

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

    total = len(products)
    pp_arg = args.get('per_page')
    if pp_arg == 'all':
        # режим «все товары сразу» (кнопка «Показать ещё» на категориях):
        # одна страница без пагинации, порядок сортировки сохраняется
        per_page = max(1, total)
        page_num = 1
        pages = 1
        items = products
    else:
        if per_page is None:
            try:
                per_page = max(1, int(pp_arg)) if pp_arg else 24
            except (TypeError, ValueError):
                per_page = 24
        try:
            page_num = max(1, int(args.get('page') or 1))
        except ValueError:
            page_num = 1
        pages = max(1, _math.ceil(total / per_page))
        page_num = min(page_num, pages)
        items = products[(page_num - 1) * per_page: page_num * per_page]

    categories = sorted({p.get('category') for p in get_products() if p.get('category')})
    # Размеры/цвета для выпадающих списков фильтра: парсим из имён товаров
    # (у WB/Ozon размеры и цвета живут в названии карточки).
    size_tokens = set()
    color_tokens = set()
    for p in get_products():
        nm = _norm_text(p.get('name'))
        for m in _re.finditer(r'(\d{2,3}x\d{2,3})', nm):
            size_tokens.add(m.group(1))
        for c in re.findall(r'(бел\w+|сер\w+|голуб\w+|син\w+|кремов\w+|розов\w+'
                            r'|бежев\w+|зелен\w+|зелён\w+|коричнев\w+|черн\w+)', nm):
            color_tokens.add(c)
    applied_sizes = sizes
    applied_colors = _arg_list('color')
    # Готовая querystring (без page) для ссылок пагинации: пустые значения
    # не включаем — URL остаётся чистым.
    from urllib.parse import urlencode as _urlencode
    qs_parts = {}
    if args.get('q'):
        qs_parts['q'] = args.get('q')
    if source:
        qs_parts['source'] = source
    if category:
        qs_parts['category'] = category
    if args.get('in_stock'):
        qs_parts['in_stock'] = '1'
    if price_from:
        qs_parts['price_from'] = price_from
    if price_to:
        qs_parts['price_to'] = price_to
    if sort != 'popular':
        qs_parts['sort'] = sort
    if applied_sizes:
        qs_parts['size'] = applied_sizes[0]
    if applied_colors:
        qs_parts['color'] = applied_colors[0]
    if section:
        qs_parts['section'] = section
    if args.get('per_page') == 'all':
        qs_parts['per_page'] = 'all'
    elif per_page and per_page != 24:
        qs_parts['per_page'] = str(per_page)

    # Плитки подкатегорий раздела (как у оригинала: «Наматрасники на молнии —
    # 64 товара»): children текущего раздела из меню сайта; счётчик и фото —
    # из товаров раздела, имена которых содержат ключевое слово подраздела.
    # Товары без совпадения не выводят плитку со счётчиком (в демо-каталоге
    # привязки товаров к подразделам нет — по ТЗ неизвестное не выводится).
    subsections = []
    if section:
        _SUB_KEYS = {
            'namatrasniki-na-molnii': ('молни',),
            'namatrasniki-na-rezinke': ('резинк',),
            'nepromokaemye-namatrasniki': ('непромокаем',),
            'zimnie': ('зимн',),
            'vsesezonnye': ('всесезон',),
            'letnie': ('летн',),
        }
        try:
            menu = load_content().get('menu') or []
            sec_url = f'/catalog/{section}/'
            for top in menu:
                for child in (top.get('children') or []):
                    if (child.get('url') or '') != sec_url:
                        continue
                    all_products = list(get_products())
                    all_products = [p for p in all_products
                                    if not p.get('site_section')
                                    or p.get('site_section') == section]
                    for sub in (child.get('children') or []):
                        slug = (sub.get('url') or '').rstrip('/').split('/')[-1]
                        keys = _SUB_KEYS.get(slug, ())
                        matched = [p for p in all_products
                                   if keys and all(
                                       k in (p.get('name') or '').lower()
                                       for k in keys)]
                        subsections.append({
                            'title': sub.get('title') or '',
                            'url': sub.get('url') or '',
                            'count': len(matched) if matched else 0,
                            'photo': matched[0].get('photo') if matched else '',
                        })
        except Exception:
            subsections = []

    return {
        'products': items,
        'total': total,
        'page': page_num,
        'pages': pages,
        'per_page': 'all' if args.get('per_page') == 'all' else per_page,
        'categories': categories,
        'sizes': sorted(size_tokens),
        'colors': sorted(color_tokens),
        'base': base,
        'show_filters': show_filters,
        'section': section,
        'subsections': subsections,
        'qs': _urlencode(qs_parts),
        'applied': {
            'q': args.get('q') or '', 'source': source, 'category': category,
            'in_stock': bool(args.get('in_stock')),
            'price_from': args.get('price_from') or '', 'price_to': args.get('price_to') or '',
            'sort': sort, 'size': applied_sizes[0] if applied_sizes else '',
            'size_all': applied_sizes, 'color': applied_colors[0] if applied_colors else '',
            'color_all': applied_colors,
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


@app.route('/catalog/site/<int:product_id>/')
def site_product_page(product_id):
    """Карточка товара, созданного в админке (без привязки к WB/Ozon)."""
    return _product_page('site', product_id)


def _product_page(source, nm_id):
    """Единая карточка товара для всех источников (CT-2…CT-4, OZ-3)."""
    product = next((p for p in get_products()
                    if p.get('source') == source
                    and int(p.get('nm_id') or p.get('id') or 0) == nm_id), None)
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

    # SEO title/description из шаблонов с переменными {name} {brand} {price}… (SEO-2);
    # явные seo_title/seo_description товара (из админки, extra) имеют приоритет.
    seo = content.get('site', {}).get('seo', {})
    page = dict(get_page_by_slug(content, 'catalog__namatrasniki'))
    page['title'] = _render_meta_template(seo.get('product_title_template'), product, content.get('site', {})) or page.get('title', '')
    page['meta_description'] = _render_meta_template(seo.get('product_description_template'), product, content.get('site', {})) or page.get('meta_description', '')
    pextra = product.get('extra') if isinstance(product.get('extra'), dict) else {}
    if pextra.get('seo_title'):
        page['title'] = str(pextra['seo_title'])
    if pextra.get('seo_description'):
        page['meta_description'] = str(pextra['seo_description'])
    # Детальный блок товара вместо статичной ленты раздела: страница товара
    # показывает сам товар (фото, имя, цена, описание, кнопки) — иначе по
    # карточке WB/Ozon отдавалась просто категорийная вёрстка без данных.
    page['content_html'] = _product_detail_html(product, site=content.get('site', {}))

    ctx = make_context(content, page)
    ctx['seo_product'] = _seo_product(product, request.host_url)
    ctx['seo_og_image'] = _absolute_url(product.get('photo'), request.host_url) or ctx['seo_og_image']
    # Страница товара рендерится шаблоном раздела: заголовок раздела и блок
    # каталога на ней не нужны — только карточка товара и рекомендации.
    ctx['is_product_page'] = True
    # Крошки и canonical карточки: Главная → Каталог → товар (SEO-1/SEO-4)
    product_url = request.host_url.rstrip('/') + request.path
    ctx['seo_canonical'] = product_url
    ctx['seo_breadcrumbs'] = [
        {'name': 'Главная', 'url': request.host_url},
        {'name': 'Каталог', 'url': request.host_url.rstrip('/') + '/catalog/'},
        {'name': product.get('name', ''), 'url': product_url},
    ]
    return Response(_inject_lazyload_fix(
        render_template('pages/catalog__namatrasniki.html', **ctx, wb_product=product)))


def _product_size_siblings(product):
    """Товары-«соседи» того же типа в других размерах.

    Размеры у WB/Ozon живут в названии («Наматрасник 200х200 …»), каждый
    размер — отдельный товар. Для переключателя размеров на карточке
    (как на оригинальном сайте) ищем товары того же источника, у которых
    имя совпадает после удаления токена размера NxM. Возвращает список
    (размер, товар), отсортированный по числовому размеру; пустой — если
    соседей нет (размерный ряд из одного товара).
    """
    import re as _re

    def _norm(s):
        s = str(s or '').lower()
        for ch in ('х', '*', '×'):
            s = s.replace(ch, 'x')
        return _re.sub(r'\s+', ' ', s).strip()

    def _strip_size(s):
        # размерный ряд определяем по «стволу» имени: без токена NxM и без
        # фразы «высота N-M см» (она отличается у разных карточек ряда)
        s = _re.sub(r'высота\s*\d+(?:-\d+)?\s*см', '', _norm(s))
        s = _re.sub(r'\d{2,3}x\d{2,3}(?:x\d{2,3})?(?:\s*см)?', '', s)
        return _re.sub(r'\s+', ' ', s).strip()

    key = _strip_size(product.get('name'))
    if not key:
        return []
    siblings = []
    for p in get_products():
        if p.get('source') != product.get('source'):
            continue
        if _strip_size(p.get('name')) != key:
            continue
        m = _re.search(r'(\d{2,3})x(\d{2,3})', _norm(p.get('name')))
        if not m:
            continue
        siblings.append((int(m.group(1)) * int(m.group(2)),
                         f'{m.group(1)}x{m.group(2)}', p))
    if len(siblings) < 2:
        return []
    # один размер может встречаться в нескольких карточках ряда (с высотой
    # борта и без) — в переключателе размер дублировать не нужно
    seen = set()
    unique = []
    for _, size, p in sorted(siblings, key=lambda x: x[0]):
        if size in seen:
            continue
        seen.add(size)
        unique.append((size, p))
    if len(unique) < 2:
        return []
    return unique


def _money(value, kopecks=True):
    """Формат цены как на оригинале: «4 146.21» (тысячи — через пробел)."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    s = f'{v:,.2f}' if kopecks else f'{v:,.0f}'
    return s.replace(',', ' ')


app.add_template_global(_money, 'money')


def _product_variants(product):
    """Группы вариантов карточки: цвет / высота борта / размер.

    Товары-«соседи» того же источника с тем же «стволом» имени (имя без
    токена размера NxM, фразы «высота N-M см» и слов цвета). Возвращает dict
    {'sizes': [(размер, товар)], 'heaths': [(высота, товар)],
    'colors': [(цвет, товар)]} с уникальными значениями (пустые группы
    опускаются); товар в каждой группе — ссылка на вариант с этим значением,
    максимально близкий к текущему (тот же размер/цвет/высота). Текущий товар
    входит в каждую группу — как на оригинале, активное значение подсвечено.
    """
    import re as _re

    colors = ('белый с узором', 'светло-коричневый', 'светло-серый',
              'темно-серый', 'тёмно-серый', 'черный', 'чёрный', 'бежевый',
              'серый', 'белый', 'коричневый')

    def _norm(s):
        s = str(s or '').lower()
        for ch in ('х', '*', '×'):
            s = s.replace(ch, 'x')
        return _re.sub(r'\s+', ' ', s).strip()

    def _size(s):
        m = _re.search(r'(\d{2,3})x(\d{2,3})', _norm(s))
        return f'{m.group(1)}x{m.group(2)}' if m else ''

    def _height(s):
        m = _re.search(r'высота\s*(\d+(?:-\d+)?)\s*см', _norm(s))
        return m.group(1) if m else ''

    def _color(s):
        s = _norm(s)
        for c in colors:
            if c in s:
                return c
        return ''

    def _stem(s):
        s = _re.sub(r'высота\s*\d+(?:-\d+)?\s*см', '', _norm(s))
        s = _re.sub(r'\d{2,3}x\d{2,3}(?:x\d{2,3})?(?:\s*см)?', '', s)
        for c in colors:
            s = s.replace(c, '')
        return _re.sub(r'\s+', ' ', s).strip()

    name = product.get('name')
    key = _stem(name)
    pid = str(product.get('nm_id') or product.get('id') or '')
    group = []
    if key and _size(name):
        for p in get_products():
            if p.get('source') != product.get('source'):
                continue
            if not _size(p.get('name')) or _stem(p.get('name')) != key:
                continue
            group.append(p)
    ids = {str(p.get('nm_id') or p.get('id') or '') for p in group}
    has_group = len(group) >= 2 and pid in ids

    attrs = (_size, _height, _color)

    def _pick(attr, value):
        others = [a for a in attrs if a is not attr]
        cand = [q for q in group if attr(q.get('name')) == value]
        for q in cand:  # вариант, отличающийся только этим атрибутом
            if all(o(q.get('name')) == o(name) for o in others):
                return q
        for q in cand:  # иначе — хотя бы с тем же размером
            if _size(q.get('name')) == _size(name):
                return q
        return cand[0]

    def _unique(attr):
        seen, out = set(), []
        for p in group:
            v = attr(p.get('name'))
            if not v or v in seen:
                continue
            seen.add(v)
            out.append((v, _pick(attr, v)))
        return out

    # Явный список вариантов из админки (extra['variants'], группы
    # colors/heights/sizes): чипы показываем именно из него, в заданном
    # порядке. Ссылку на товар-вариант подставляем из авто-группы, если
    # нашёлся сосед с таким значением, иначе чип без ссылки.
    extra = product.get('extra')
    manual = extra.get('variants') if isinstance(extra, dict) else None
    if isinstance(manual, dict) and manual:
        auto = {}
        if has_group:
            auto = {'sizes': _unique(_size), 'heights': _unique(_height),
                    'colors': _unique(_color)}
        current = {'sizes': _size(name), 'heights': _height(name),
                   'colors': _color(name)}
        explicit = {}
        for grp in ('sizes', 'heights', 'colors'):
            values = manual.get(grp)
            if not isinstance(values, list):
                continue
            items = []
            for v in values:
                v = str(v).strip()
                if not v:
                    continue
                target = next((p for val, p in auto.get(grp, [])
                               if str(val).lower() == v.lower()), None)
                items.append((v, target))
            if items:
                explicit[grp] = items
        if explicit:
            return explicit

    if not has_group:
        return {}

    variants = {}
    sizes = _unique(_size)
    if len(sizes) >= 2:
        sizes.sort(key=lambda x: int(x[0].split('x')[0]) * int(x[0].split('x')[1]))
        variants['sizes'] = sizes
    heights = _unique(_height)
    if len(heights) >= 2:
        heights.sort(key=lambda x: int(x[0].split('-')[0]))
        variants['heights'] = heights
    found_colors = _unique(_color)
    if len(found_colors) >= 2:
        variants['colors'] = found_colors
    return variants


def _product_detail_html(product, site=None):
    """Детальный блок карточки товара для /catalog/<source>/<nm_id>/.

    Вёрстка по оригинальному aspro-дизайну: слева галерея, в центре —
    избранное/рейтинг, группы вариантов (Цвет / Высота борта / Размер —
    чипы-ссылки на товары-соседи), компактные «Характеристики» с раскрытием
    «Все характеристики», справа — карточка покупки (цена с копейками,
    бейдж «-N% Экономия», степпер количества, «В корзину» / «Купить в 1 клик»
    — те же классы wb-cart-btn/wb-buy-btn/wb-like-btn + форма .wb-order-form,
    обработчики уже живут на страницах), доставка/«Хочу в подарок» и блок
    консультации; ниже — вкладки Описание/Характеристики.
    Подписи блоков («Нашли дешевле?», «Хочу в подарок», текст доставки,
    «Нужна консультация?») читаются из настроек сайта (site.*) с дефолтами.
    """
    from markupsafe import escape as _esc
    import urllib.parse as _uparse
    import re as _re

    site = site or {}

    def _site_label(key, default):
        # Подписи блоков карточки — из настроек сайта; пусто/пробелы -> дефолт.
        val = str(site.get(key) or '').strip()
        return val[:200] if val else default

    label_cheaper = _site_label('product_cheaper_label', 'Нашли дешевле?')
    label_delivery = _site_label('product_delivery_text',
                                 'Доставка по России — при оформлении заказа')
    label_gift = _site_label('product_gift_label', 'Хочу в подарок')
    label_consult_title = _site_label('product_consult_title', 'Нужна консультация?')
    label_consult_text = _site_label(
        'product_consult_text', 'Наш специалист ответит на любой интересующий вопрос')
    label_consult_button = _site_label('product_consult_button', 'Задать вопрос')

    name = str(product.get('name') or 'Товар')
    src = product.get('source') or 'wb'
    pid = product.get('nm_id') or product.get('id') or ''
    photo = product.get('photo') or ''
    # Галерея изображений (extra['gallery'], задаётся в админке): главное —
    # photo (или первое из галереи, если фото нет), остальные — миниатюры.
    gallery = product.get('gallery')
    if not isinstance(gallery, list):
        gallery = []
    if not photo and gallery:
        photo = str(gallery[0])
    if photo and photo.startswith(('http://', 'https://')):
        photo = '/proxy/image?url=' + _uparse.quote(photo, safe='')
    price = int(product.get('price') or 0)
    final_price = int(product.get('final_price') or price or 0)
    stock = int(product.get('stock') or 0)
    brand = str(product.get('brand') or 'Экотория')
    # Зачёркнутая «старая» цена — прайс WB до скидки; продажа — по final_price.
    base = price
    desc = str(product.get('description') or '').strip()
    site_badge = product.get('site_discount_badge')

    nm_norm = name.lower().replace('х', 'x')
    m_size = _re.search(r'(\d{2,3})x(\d{2,3})', nm_norm)
    cur_size = f'{m_size.group(1)}x{m_size.group(2)}' if m_size else ''
    m_height = _re.search(r'высота\s*(\d+(?:-\d+)?)\s*см', nm_norm)
    cur_height = m_height.group(1) if m_height else ''

    def _cur_color():
        for c in ('белый с узором', 'светло-коричневый', 'светло-серый',
                  'темно-серый', 'тёмно-серый', 'черный', 'чёрный', 'бежевый',
                  'серый', 'белый', 'коричневый'):
            if c in nm_norm:
                return c
        return ''

    cur_color = _cur_color()

    # Карточка покупки: цена с копейками, зачёркнутая базовая, бейдж скидки.
    price_html = ''
    badge_html = ''
    if base and final_price < base:
        pct = round((base - final_price) * 100 / base)
        price_html = (
            f'<span style="color:#222;font-weight:700;font-size:1.55rem;white-space:nowrap;">'
            f'{_money(final_price)} ₽</span>'
            f'<span style="color:#777;font-size:.95rem;">/шт</span>'
            f'<span style="text-decoration:line-through;color:#999;font-size:1rem;margin-left:.5rem;white-space:nowrap;">'
            f'{_money(base, kopecks=False)} ₽</span>')
        badge_html = (
            '<div style="display:flex;gap:.4rem;align-items:center;margin-top:.4rem;flex-wrap:wrap;">'
            f'<span style="background:#ffcf3f;color:#333;font-size:.72rem;font-weight:700;'
            f'padding:.14rem .45rem;border-radius:3px;white-space:nowrap;">-{pct}%</span>'
            f'<span style="background:#ffe9a8;color:#6b5200;font-size:.78rem;padding:.14rem .5rem;'
            f'border-radius:3px;white-space:nowrap;">Экономия {_money(base - final_price)} ₽</span>'
            + (f'<span style="background:#137333;color:#fff;font-size:.72rem;padding:.14rem .5rem;'
               f'border-radius:3px;white-space:nowrap;">скидка {site_badge}% на сайте</span>' if site_badge else '')
            + '</div>')
    else:
        price_html = (
            f'<span style="color:#222;font-weight:700;font-size:1.55rem;white-space:nowrap;">'
            f'{_money(final_price or base)} ₽</span>'
            f'<span style="color:#777;font-size:.95rem;">/шт</span>')

    if stock <= 0:
        stock_color = '#c00'
    elif stock <= 5:
        stock_color = '#b26a00'
    else:
        stock_color = '#2e7d32'
    # текст бейджа: свой из админки (extra['stock_label']) или авто по остатку
    custom_label = str(product.get('stock_label') or '').strip()
    if custom_label:
        stock_label = custom_label[:100]
    elif stock <= 0:
        stock_label = 'Нет в наличии'
    elif stock <= 5:
        stock_label = 'Осталось мало'
    else:
        stock_label = 'Много'
    stock_html = (
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
        f'background:{stock_color};margin-right:.35rem;"></span>'
        f'<span style="color:{stock_color};font-weight:600;">{_esc(stock_label)}</span>')

    # Галерея: главное фото + миниатюры (клик подменяет главное).
    images = []
    if photo:
        images.append(photo)
    for g in gallery:
        g = str(g or '')
        if not g:
            continue
        if g.startswith(('http://', 'https://')):
            g = '/proxy/image?url=' + _uparse.quote(g, safe='')
        if g not in images:
            images.append(g)
    if images:
        thumbs = ''
        if len(images) > 1:
            thumbs = ('<div style="display:flex;gap:.5rem;flex-wrap:wrap;'
                      'justify-content:center;margin-top:.7rem;">' + ''.join(
                f'<img src="{_esc(u)}" alt="" loading="lazy" onclick="var m=document.getElementById(\'wb-main-img\');'
                'if(m)m.src=this.src;" '
                'style="width:64px;height:64px;object-fit:contain;border:1px solid #eee;'
                'border-radius:6px;cursor:pointer;background:#fff;padding:2px;">'
                for u in images) + '</div>')
        img_html = (
            f'<img id="wb-main-img" src="{_esc(images[0])}" alt="{_esc(name)}" '
            'style="width:100%;max-width:460px;object-fit:contain;display:block;margin:0 auto;">'
            + thumbs)
    else:
        img_html = ''

    # Бейджи из админки («Хит»/«Новинка», extra['badges']) — поверх фото.
    raw_badges = product.get('badges')
    if isinstance(raw_badges, list) and raw_badges:
        badge_names = {'hit': 'Хит', 'new': 'Новинка'}
        badges_html = ''.join(
            f'<span style="display:inline-block;background:#b49d84;color:#fff;'
            f'font-size:.72rem;font-weight:700;padding:.18rem .55rem;'
            f'border-radius:3px;margin:0 .35rem .35rem 0;">'
            f'{_esc(badge_names.get(str(b), str(b)))}</span>'
            for b in raw_badges if badge_names.get(str(b)) or str(b).strip())
        img_html = ('<div style="position:relative;">'
                    f'<div style="position:absolute;top:.4rem;left:.4rem;z-index:2;">'
                    f'{badges_html}</div>{img_html}</div>')

    # Видео из админки (extra['video_url']) — iframe под галереей (16:9).
    video_url = str(product.get('video_url') or '').strip()
    if video_url and video_url.startswith(('http://', 'https://')):
        # youtube/vimeo/обычный mp4 — универсальный embed через iframe
        embed_url = video_url
        m_yt = _re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{6,})', video_url)
        if m_yt:
            embed_url = f'https://www.youtube.com/embed/{m_yt.group(1)}'
        img_html += (
            '<div style="margin-top:.8rem;position:relative;'
            'padding-top:56.25%;max-width:460px;margin-left:auto;margin-right:auto;">'
            f'<iframe src="{_esc(embed_url)}" loading="lazy" allowfullscreen '
            'style="position:absolute;top:0;left:0;width:100%;height:100%;'
            'border:0;border-radius:6px;"></iframe></div>')

    # Группы вариантов (чипы-ссылки на товары-соседи; активный — заливка темы).
    variants = _product_variants(product)

    def _chips_group(title, items, current, fmt=None):
        chips = []
        for value, p in items:
            label = fmt(value) if fmt else value
            active = str(value).lower() == str(current or '').lower()
            if active:
                chips.append(
                    f'<span style="display:inline-block;padding:.4rem .8rem;border:1px solid #b49d84;'
                    f'background:#b49d84;color:#fff;border-radius:4px;font-size:.83rem;">{_esc(label)}</span>')
            elif p is None:
                # явный вариант из админки без товара-ссылки — просто чип
                chips.append(
                    f'<span style="display:inline-block;padding:.4rem .8rem;border:1px solid #ddd;'
                    f'color:#333;border-radius:4px;font-size:.83rem;">{_esc(label)}</span>')
            else:
                spid = str(p.get('nm_id') or p.get('id') or '')
                chips.append(
                    f'<a href="/catalog/{_esc(src)}/{_esc(spid)}/" '
                    f'style="display:inline-block;padding:.4rem .8rem;border:1px solid #ddd;'
                    f'color:#333;border-radius:4px;font-size:.83rem;text-decoration:none;">'
                    f'{_esc(label)}</a>')
        return (
            '<div style="margin:0 0 16px;">'
            f'<div style="font-size:.82rem;color:#666;margin-bottom:.45rem;">{_esc(title)}</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:.4rem;">{"".join(chips)}</div></div>')

    chips_html = ''
    if variants.get('colors'):
        chips_html += _chips_group(
            'Цвет', variants['colors'], cur_color,
            fmt=lambda v: v[0].upper() + v[1:])
    if variants.get('heights'):
        chips_html += _chips_group(
            'Высота борта, см', variants['heights'], cur_height,
            fmt=lambda v: f'{v} см')
    if variants.get('sizes'):
        chips_html += _chips_group('Размер, см', variants['sizes'], cur_size)

    # Характеристики (панель под чипами + полный список во вкладке):
    # известное — из имени/типа товара, неизвестное — прочерк (как оригинал).
    is_mattress = 'наматрасник' in nm_norm
    is_blanket = 'одеяло' in nm_norm
    filler = 'Синтепон' if (is_mattress or is_blanket) else ''
    waterproof = 'Да' if 'непромокаем' in nm_norm else '—'
    if is_mattress:
        if 'на молнии' in nm_norm:
            item_name = 'Наматрасник на молнии'
        elif 'резинк' in nm_norm or '4d-fix' in nm_norm or '4d-фикс' in nm_norm:
            item_name = 'Наматрасник на резинке'
        else:
            item_name = 'Наматрасник'
    elif is_blanket:
        item_name = 'Одеяло'
    else:
        item_name = ''
    completeness = f'{item_name} - 1шт. Заводская упаковка - 1шт.' if item_name else '—'
    purpose = 'Взрослая' if (is_mattress or is_blanket) else '—'

    main_chars = []
    if filler:
        main_chars.append(('Наполнитель', filler))
    main_chars.append(('Толщина (см)', cur_height or '—'))
    if is_mattress:
        main_chars.append(('Водонепроницаемость', waterproof))
        main_chars.append(('Комплектация', completeness))
        main_chars.append(('Назначение текстиля', purpose))
        if cur_size:
            main_chars.append(('Размер наматрасника', cur_size))
    elif cur_size:
        main_chars.append(('Размер', cur_size))
    if cur_color:
        main_chars.append(('Цвет', cur_color[0].upper() + cur_color[1:]))
    extra_chars = [('Артикул', str(pid)), ('Бренд', brand),
                   ('Источник', 'Wildberries' if src == 'wb' else
                    ('Ozon' if src == 'ozon' else 'Сайт Экотория'))]
    # Характеристики из админки (extra товара, редактируются в модалке) —
    # показываем в общем списке: в панели после авто-характеристик,
    # во вкладке «Характеристики» вместе со всеми.
    admin_chars = []
    raw_chars = product.get('characteristics')
    if isinstance(raw_chars, dict):
        admin_chars = [(str(k), str(v)) for k, v in raw_chars.items()
                       if str(k).strip() and str(v).strip()]

    def _char_rows(pairs, bordered=True):
        border = 'border-bottom:1px solid #f2f2f2;' if bordered else ''
        return ''.join(
            f'<div style="display:flex;gap:.6rem;padding:.32rem 0;font-size:.85rem;{border}">'
            f'<div style="min-width:150px;color:#888;flex-shrink:0;">{_esc(k)}</div>'
            f'<div style="color:#333;">{_esc(v)}</div></div>'
            for k, v in pairs)

    chars_panel = (
        '<div style="margin-top:20px;">'
        '<div style="font-weight:700;font-size:1rem;margin-bottom:.5rem;">Характеристики</div>'
        f'{_char_rows(main_chars + admin_chars, bordered=False)}'
        '<div class="wb-chars-extra" hidden>'
        f'{_char_rows(extra_chars, bordered=False)}</div>'
        '<a href="#" class="wb-chars-more" '
        'style="font-size:.82rem;color:#b49d84;text-decoration:underline dotted;">'
        'Все характеристики</a></div>')

    chars_tab = f'<div>{_char_rows(main_chars + admin_chars + extra_chars)}</div>'

    desc_html = (f'<div style="line-height:1.7;color:#333;white-space:pre-line;">{_esc(desc)}</div>'
                 if desc else
                 '<div style="color:#888;">Описание появится в ближайшее время.</div>')

    # Состав / уход / размерная сетка — правки из админки (extra), блоками
    # под описанием (заголовок + текст, как секции карточки на оригинале).
    def _desc_section(title, text):
        return (
            f'<div style="margin-top:1.4rem;"><div style="font-weight:700;'
            f'font-size:1rem;margin-bottom:.5rem;">{_esc(title)}</div>'
            f'<div style="line-height:1.7;color:#333;white-space:pre-line;">'
            f'{_esc(text)}</div></div>')

    for _title, _key in (('Состав', 'composition'), ('Уход', 'care'),
                         ('Размерная сетка', 'size_chart')):
        _val = str(product.get(_key) or '').strip()
        if _val:
            desc_html += _desc_section(_title, _val)

    def _tab_btn(tab, label, active):
        return (
            f'<button type="button" data-tab="{tab}" '
            'style="padding:.55rem 1.1rem;background:#fff;border:1px solid '
            f'{"#b49d84" if active else "#e5e5e5"};font-size:.8rem;letter-spacing:.6px;'
            'text-transform:uppercase;cursor:pointer;'
            f'color:{"#333" if active else "#888"};font-weight:{600 if active else 400};">'
            f'{label}</button>')

    tabs_html = (
        '<div class="wb-detail-tabs" style="margin-top:40px;">'
        '<div class="wb-detail-tabs__nav" style="display:flex;gap:.4rem;flex-wrap:wrap;">'
        + _tab_btn('desc', 'Описание', True)
        + _tab_btn('chars', 'Характеристики', False)
        + '</div>'
        '<div class="wb-detail-tabs__pane" data-pane="desc" style="padding:1.2rem 0;">'
        f'{desc_html}</div>'
        f'<div class="wb-detail-tabs__pane" data-pane="chars" hidden '
        f'style="padding:1.2rem 0;">{chars_tab}</div>'
        '<script>(function(){'
        'if(window.__wbDetailTabs)return;window.__wbDetailTabs=1;'
        "document.addEventListener('click',function(e){"
        "var b=e.target.closest&&e.target.closest('.wb-detail-tabs__nav button');"
        'if(b){var t=b.closest(".wb-detail-tabs");'
        't.querySelectorAll(".wb-detail-tabs__nav button").forEach(function(x){'
        'var on=x===b;'
        'x.style.color=on?"#333":"#888";x.style.borderColor=on?"#b49d84":"#e5e5e5";'
        'x.style.fontWeight=on?"600":"400";});'
        't.querySelectorAll(".wb-detail-tabs__pane").forEach(function(p){'
        'p.hidden=p.getAttribute("data-pane")!==b.getAttribute("data-tab");});}'
        "var a=e.target.closest&&e.target.closest('.wb-chars-more');"
        'if(a){e.preventDefault();var box=a.parentElement.querySelector(".wb-chars-extra");'
        'if(box){box.hidden=!box.hidden;'
        'a.textContent=box.hidden?"Все характеристики":"Свернуть";}}'
        "var s=e.target.closest&&e.target.closest('.wb-qty button');"
        'if(s){var inp=s.parentElement.querySelector("input");'
        'var v=parseInt(inp.value||"1",10)+parseInt(s.getAttribute("data-step"),10);'
        'if(v<1)v=1;if(v>99)v=99;inp.value=v;}'
        '});})();</script>'
        '</div>')

    # Внешняя ссылка на маркетплейс — только второстепенная: основная покупка
    # (корзина/1 клик) происходит на сайте. Режим «Скрыть маркетплейсы» (8.3) —
    # ссылку и «Нашли дешевле?» не показываем вовсе.
    hide_mp = bool(site.get('hide_marketplaces'))
    ext_url = str(product.get('url') or '').strip()
    ext_html = ''
    if ext_url.startswith(('http://', 'https://')) and not hide_mp:
        mp_name = 'Ozon' if src == 'ozon' else 'Wildberries'
        ext_html = (
            '<div style="margin-top:14px;font-size:0.85rem;">'
            f'<a href="{_esc(ext_url)}" target="_blank" rel="noopener" '
            'style="color:#888;text-decoration:underline;">'
            f'Посмотреть на {mp_name} ↗</a></div>')

    disabled = stock <= 0
    buy_style = ('margin-top:.6rem;width:100%;background:#ccc;color:#fff;border:0;padding:.7rem;'
                 'border-radius:6px;cursor:not-allowed;font-weight:600;' if disabled else
                 'margin-top:.6rem;width:100%;background:#fff;color:#333;border:1px solid #ccc;'
                 'padding:.7rem;border-radius:6px;cursor:pointer;font-weight:600;'
                 'text-transform:uppercase;letter-spacing:1px;')
    order_form = ''
    if not disabled:
        order_form = (
            f'<form class="wb-order-form" data-id="{_esc(str(pid))}" style="display:none;margin-top:.75rem;'
            'padding:.75rem;background:#f9f9f9;border-radius:6px;">'
            '<input type="text" name="name" placeholder="Ваше имя" required style="width:100%;margin-bottom:.4rem;padding:.4rem;border:1px solid #ddd;border-radius:4px;">'
            '<input type="tel" name="phone" placeholder="Телефон" required style="width:100%;margin-bottom:.4rem;padding:.4rem;border:1px solid #ddd;border-radius:4px;">'
            '<input type="email" name="email" placeholder="Email (необязательно)" style="width:100%;margin-bottom:.4rem;padding:.4rem;border:1px solid #ddd;border-radius:4px;">'
            '<label style="display:flex;align-items:flex-start;gap:.35rem;font-size:.75rem;color:#666;margin-bottom:.5rem;cursor:pointer;">'
            '<input type="checkbox" name="consent_pd" value="1" required style="margin-top:.1rem;">'
            '<span>Соглашаюсь с обработкой персональных данных</span></label>'
            '<button type="submit" style="width:100%;background:#137333;color:#fff;border:0;padding:.5rem;border-radius:4px;cursor:pointer;font-weight:600;">Оформить заявку</button>'
            '<div class="wb-order-result" style="margin-top:.4rem;font-size:.8rem;"></div></form>')

    stepper_html = (
        '<div class="wb-qty" style="display:flex;border:1px solid #ddd;border-radius:6px;'
        'overflow:hidden;flex-shrink:0;">'
        '<button type="button" data-step="-1" '
        'style="width:34px;background:#f7f7f7;border:0;border-right:1px solid #eee;'
        'cursor:pointer;font-size:1rem;color:#555;">−</button>'
        '<input type="text" value="1" readonly '
        'style="width:42px;text-align:center;border:0;font-size:.95rem;color:#333;">'
        '<button type="button" data-step="1" '
        'style="width:34px;background:#f7f7f7;border:0;border-left:1px solid #eee;'
        'cursor:pointer;font-size:1rem;color:#555;">+</button></div>')

    cart_btn_style = ('flex:1;background:#ccc;color:#fff;border:0;padding:.7rem;border-radius:6px;'
                      'cursor:not-allowed;font-weight:600;text-transform:uppercase;'
                      'letter-spacing:.4px;' if disabled else
                      'flex:1;background:#b49d84;color:#fff;border:0;padding:.7rem;border-radius:6px;'
                      'cursor:pointer;font-weight:600;text-transform:uppercase;letter-spacing:.4px;')

    rating = 0
    try:
        rating = round(float(product.get('rating') or 0))
    except (TypeError, ValueError):
        rating = 0
    stars_html = (
        f'<span style="color:#f5a623;font-size:.95rem;letter-spacing:2px;" title="Рейтинг">'
        f'{"★" * rating}</span>'
        f'<span style="color:#ddd;font-size:.95rem;letter-spacing:2px;">{"★" * (5 - rating)}</span>')

    like_btn = (
        f'<button type="button" class="wb-like-btn" data-id="{_esc(str(pid))}" data-name="{_esc(name)}" '
        f'data-price="{final_price}" data-photo="{_esc(str(product.get("photo") or ""))}" '
        f'data-url="/catalog/{_esc(src)}/{_esc(str(pid))}/" data-source="{_esc(src)}" '
        'style="background:#fff;border:1px solid #ddd;border-radius:20px;color:#555;'
        'cursor:pointer;font-size:.78rem;padding:.35rem .95rem;letter-spacing:.4px;">'
        '♡ В ИЗБРАННОЕ</button>')

    section = str(product.get('site_section') or '').strip()
    section_link = (f'<a href="/catalog/{_esc(section)}/" style="color:#888;text-decoration:none;">'
                    f'{_esc(section)}</a> / ' if section else '')

    return (
        '<div class="wb-detail" style="max-width:1408px;margin:0 auto;padding:24px 16px 48px;">'
        '<style>@media (max-width:1100px){.wb-detail-grid{grid-template-columns:1fr !important;'
        'gap:28px !important;}.wb-detail h1{font-size:1.3rem !important;}}'
        '@media (min-width:1101px){.wb-buy-card{position:sticky;top:16px;}}</style>'
        '<div style="font-size:.85rem;color:#888;margin-bottom:14px;">'
        '<a href="/" style="color:#888;text-decoration:none;">Главная</a> / '
        '<a href="/catalog/" style="color:#888;text-decoration:none;">Каталог</a> / '
        f'{section_link}<span>{_esc(name)}</span></div>'
        f'<h1 id="pagetitle" style="font-size:1.75rem;line-height:1.3;margin:0 0 24px;font-weight:600;">{_esc(name)}</h1>'
        '<div class="wb-detail-grid" style="display:grid;'
        'grid-template-columns:minmax(260px,460px) minmax(300px,1fr) 340px;gap:36px;align-items:start;">'
        f'<div style="border:1px solid #f0f0f0;border-radius:8px;padding:16px;background:#fff;">{img_html}</div>'
        '<div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem;margin-bottom:10px;">'
        f'{like_btn}<span>{stars_html}</span></div>'
        f'<div style="font-size:.82rem;color:#999;margin-bottom:16px;">Артикул: {_esc(str(pid))}'
        f'&nbsp;&nbsp;Бренд: {_esc(brand)}</div>'
        f'{chips_html}'
        f'{chars_panel}'
        '</div>'
        '<div>'
        '<div class="wb-buy-card" style="background:#fff;border:1px solid #eee;border-radius:8px;'
        'box-shadow:0 2px 12px rgba(0,0,0,.06);padding:20px;">'
        f'<div style="display:flex;align-items:baseline;gap:.4rem;flex-wrap:wrap;">{price_html}</div>'
        f'{badge_html}'
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem;'
        'margin-top:.7rem;font-size:.85rem;flex-wrap:wrap;">'
        f'<span>{stock_html}'
        '<span style="color:#aaa;margin-left:.3rem;cursor:help;" title="Остаток обновляется автоматически">ⓘ</span></span>'
        + ('' if hide_mp else
           f'<a href="#" style="color:#777;font-size:.82rem;text-decoration:underline;">📋 {_esc(label_cheaper)}</a>')
        + '</div>'
        '<div style="display:flex;gap:.5rem;margin-top:14px;align-items:stretch;">'
        f'{stepper_html}'
        f'<button type="button" class="wb-cart-btn" data-id="{_esc(str(pid))}" data-source="{_esc(src)}" '
        f'style="{cart_btn_style}">В корзину</button>'
        '</div>'
        f'<button type="button" class="wb-buy-btn" data-id="{_esc(str(pid))}" data-name="{_esc(name)}" '
        f'data-price="{final_price}" data-stock="{stock}"{(" disabled" if disabled else "")} '
        f'style="{buy_style}">Купить в 1 клик</button>'
        f'{order_form}'
        '<div style="margin-top:16px;font-size:.85rem;color:#555;line-height:1.6;">'
        f'🚚 {_esc(label_delivery)} '
        '<a href="/contacts/" style="color:#b49d84;text-decoration:underline;">Подробнее</a><br>'
        '<label style="display:flex;align-items:center;gap:.35rem;margin-top:.4rem;cursor:pointer;color:#777;">'
        f'<input type="checkbox" style="margin:0;">🎁 {_esc(label_gift)}</label></div>'
        f'{ext_html}'
        '</div>'
        '<div style="margin-top:18px;border:1px solid #eee;border-radius:8px;padding:18px;text-align:center;">'
        '<div style="font-size:1.5rem;">✉️</div>'
        f'<div style="font-weight:600;margin:.25rem 0;">{_esc(label_consult_title)}</div>'
        f'<div style="font-size:.82rem;color:#888;margin-bottom:.6rem;">{_esc(label_consult_text)}</div>'
        '<a href="/contacts/" style="display:block;border:1px solid #b49d84;color:#b49d84;'
        'padding:.5rem;text-decoration:none;font-size:.8rem;letter-spacing:.5px;">'
        f'{_esc(label_consult_button).upper()}</a>'
        '</div>'
        '</div></div>'
        f'{tabs_html}'
        '<div style="margin-top:30px;"><a href="/catalog/" style="color:#b49d84;text-decoration:underline;">← Все товары каталога</a></div>'
        '</div>'
    )


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
    """Лайк/анлайк товара (toggle): повторный клик снимает лайк.

    Принимает необязательный снапшот товара (price/photo/url/source), чтобы
    избранное показывало товар даже вне каталога сайта.
    """
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
    cid = session.get('customer_id')
    sid = _session_id()
    if customers.is_liked(product_id, customer_id=cid, session_id=sid):
        customers.remove_like(product_id, customer_id=cid, session_id=sid)
        liked = False
    else:
        price = data.get('price')
        try:
            price = int(round(float(price)))
        except (TypeError, ValueError):
            price = 0
        snapshot = {
            'name': product_name,
            'price': max(0, price),
            'photo': (data.get('photo') or '').strip()[:1000],
            'url': (data.get('url') or '').strip()[:500],
            'source': (data.get('source') or '').strip()[:20],
        }
        customers.add_like(product_id, product_name,
                           customer_id=cid, session_id=sid, snapshot=snapshot)
        liked = True
    invalidate_site_cache()
    return jsonify({'success': True, 'liked': liked,
                    'likes': customers.get_like_counts().get(product_id, 0)})


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
    """Дашборд (AD-3): заявки, новые клиенты, ошибки синка, популярные товары,
    непрочитанные сообщения чата (этап 7.4)."""
    from models import ChatMessage, SyncLog
    now = datetime.now(timezone.utc)
    orders_all = db.session.query(Order).all()
    orders_new = [o for o in orders_all if o.status == 'new']
    customers_all = db.session.query(Customer).all()
    new_customers = [c for c in customers_all
                     if c.created_at and (now - _aware(c.created_at)).days <= 7]
    sync_errors = [e.to_dict() for e in
                   db.session.query(SyncLog).filter_by(status='error')
                   .order_by(SyncLog.id.desc()).limit(10).all()]
    chat_unread = db.session.query(ChatMessage).filter_by(
        sender='client', read_by_admin=False).count()
    popular = customers.get_popular_products(get_products(), limit=5)
    return jsonify({
        'success': True,
        'orders_total': len(orders_all),
        'orders_new': len(orders_new),
        'orders_today': len([o for o in orders_all
                             if o.created_at and (now - _aware(o.created_at)).days < 1]),
        'customers_total': len(customers_all),
        'customers_new_7d': len(new_customers),
        'chat_unread': chat_unread,
        'sync_errors': sync_errors,
        'popular_products': [{'nm_id': p['product'].get('nm_id') or p['product'].get('id'),
                              'name': p['product'].get('name'),
                              'orders': p.get('orders', 0), 'likes': p.get('likes', 0),
                              'score': p.get('score', 0)} for p in popular],
    })


@app.route('/admin/api/sync-status')
@admin_required
def admin_api_sync_status():
    """Статус синхронизации для «Интеграций» (8.4): расписание, состояние,
    последние запуски по журналу (sync_logs)."""
    import json as _json
    from models import SyncLog
    try:
        state = _json.loads(storage.get_setting('site.mp_autosync_state') or '{}')
    except Exception:
        state = {}
    site = load_content().get('site', {})
    result = {}
    for source in ('wb', 'ozon'):
        settings = dict(WB_AUTOSYNC_DEFAULTS)
        saved = site.get('{}_autosync'.format(source))
        if isinstance(saved, dict):
            settings.update(saved)
        st = (state or {}).get(source) or {}
        last_log = (db.session.query(SyncLog).filter_by(source=source)
                    .order_by(SyncLog.id.desc()).first())
        last_ok = (db.session.query(SyncLog).filter_by(source=source, status='ok')
                   .order_by(SyncLog.id.desc()).first())
        result[source] = {
            'enabled': bool(settings.get('enabled')),
            'catalog_hours': settings.get('catalog_hours'),
            'light_minutes': settings.get('light_minutes'),
            'last_catalog': st.get('last_catalog'),
            'last_light': st.get('last_light'),
            'last_log': last_log.to_dict() if last_log else None,
            'last_ok': last_ok.to_dict() if last_ok else None,
        }
    return jsonify({'success': True, 'status': result})


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


# Подписка на рассылку из подвала сайта.
_SUBSCRIBE_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')


@app.route('/api/subscribe', methods=['POST'])
@_rate_limit(20)
def api_subscribe():
    """Публичная подписка на рассылку: email в таблицу subscribers, защита от дублей.

    Дубль — не ошибка: отвечаем success с already=True (idempotent для посетителя).
    """
    from models import Subscriber
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    email = (data.get('email') or '').strip().lower()
    if len(email) > 300 or not _SUBSCRIBE_EMAIL_RE.match(email):
        return jsonify({'success': False, 'error': 'Некорректный email'}), 400
    # source — служебная метка, только безопасные символы
    source = re.sub(r'[^\w\-.]', '', str(data.get('source') or 'footer'))[:60] or 'footer'
    existing = db.session.query(Subscriber).filter_by(email=email).first()
    if existing:
        return jsonify({'success': True, 'already': True,
                        'message': 'Вы уже подписаны на рассылку'})
    db.session.add(Subscriber(email=email, source=source))
    db.session.commit()
    return jsonify({'success': True, 'already': False,
                    'message': 'Вы подписаны на рассылку'})


@app.route('/admin/api/subscribers')
@admin_required
def admin_api_subscribers():
    """Список подписчиков рассылки для админки."""
    from models import Subscriber
    subs = db.session.query(Subscriber).order_by(Subscriber.id.desc()).all()
    return jsonify({'success': True, 'subscribers': [s.to_dict() for s in subs]})


@app.route('/admin/api/subscriber/delete', methods=['POST'])
@admin_required
def admin_api_subscriber_delete():
    """Удаление подписчика по запросу (152-ФЗ) — с аудитом."""
    from models import Subscriber
    data = request.get_json() or {}
    sid = data.get('id')
    if not sid:
        return jsonify({'success': False, 'error': 'id обязателен'}), 400
    sub = db.session.get(Subscriber, int(sid))
    if not sub:
        return jsonify({'success': False, 'error': 'Подписчик не найден'}), 404
    db.session.delete(sub)
    db.session.commit()
    _audit_log('subscriber.delete', 'subscriber', sid)
    return jsonify({'success': True})


# Заявки с кнопок конструктора (действие «форма заявки», цикл 4 / этап 5).
@app.route('/api/feedback', methods=['POST'])
@_rate_limit(20)
def api_feedback():
    """Публичная форма заявки с кнопки: name/phone/message → feedback_requests."""
    from models import FeedbackRequest
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    name = (data.get('name') or '').strip()[:200]
    phone = re.sub(r'[^\d+\-() ]', '', str(data.get('phone') or '')).strip()[:60]
    message = (data.get('message') or '').strip()[:5000]
    page = (data.get('page') or '').strip()[:300]
    if not name and not phone:
        return jsonify({'success': False, 'error': 'Укажите имя или телефон'}), 400
    db.session.add(FeedbackRequest(name=name, phone=phone, message=message, page=page))
    db.session.commit()
    return jsonify({'success': True, 'message': 'Заявка отправлена. Мы свяжемся с вами.'})


@app.route('/admin/api/feedback')
@admin_required
def admin_api_feedback():
    """Список заявок для админки."""
    from models import FeedbackRequest
    rows = db.session.query(FeedbackRequest).order_by(FeedbackRequest.id.desc()).limit(500).all()
    return jsonify({'success': True, 'feedback': [r.to_dict() for r in rows]})


@app.route('/admin/api/feedback/delete', methods=['POST'])
@admin_required
def admin_api_feedback_delete():
    """Удаление заявки — с аудитом."""
    from models import FeedbackRequest
    data = request.get_json() or {}
    fid = data.get('id')
    if not fid:
        return jsonify({'success': False, 'error': 'id обязателен'}), 400
    row = db.session.get(FeedbackRequest, int(fid))
    if not row:
        return jsonify({'success': False, 'error': 'Заявка не найдена'}), 404
    db.session.delete(row)
    db.session.commit()
    _audit_log('feedback.delete', 'feedback', fid)
    return jsonify({'success': True})


# Отзывы покупателей (цикл 4 / этап 6): форма на странице «Отзывы»,
# модерация в админке, вывод одобренных на сайте.
@app.route('/api/review', methods=['POST'])
@_rate_limit(10)
def api_review():
    """Публичная форма «Оставить отзыв»: имя/оценка/текст → reviews (на модерацию)."""
    from models import Review
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    name = (data.get('name') or '').strip()[:200]
    text = (data.get('text') or '').strip()[:8000]
    product_name = (data.get('product_name') or '').strip()[:500]
    try:
        rating = int(data.get('rating'))
    except (TypeError, ValueError):
        rating = 0
    if not name:
        return jsonify({'success': False, 'error': 'Укажите имя'}), 400
    if not text:
        return jsonify({'success': False, 'error': 'Напишите текст отзыва'}), 400
    if rating < 1 or rating > 5:
        return jsonify({'success': False, 'error': 'Оценка — от 1 до 5'}), 400
    db.session.add(Review(name=name, rating=rating, text=text,
                          product_name=product_name, approved=False))
    db.session.commit()
    return jsonify({'success': True,
                    'message': 'Спасибо! Ваш отзыв появится на сайте после проверки.'})


@app.route('/admin/api/reviews')
@admin_required
def admin_api_reviews():
    """Список отзывов для админки (сначала непроверенные)."""
    from models import Review
    rows = db.session.query(Review).order_by(Review.approved.asc(), Review.id.desc()).limit(500).all()
    return jsonify({'success': True, 'reviews': [r.to_dict() for r in rows]})


@app.route('/admin/api/review/moderate', methods=['POST'])
@admin_required
def admin_api_review_moderate():
    """Одобрить/скрыть отзыв — с аудитом."""
    from models import Review
    data = request.get_json() or {}
    rid = data.get('id')
    if not rid:
        return jsonify({'success': False, 'error': 'id обязателен'}), 400
    row = db.session.get(Review, int(rid))
    if not row:
        return jsonify({'success': False, 'error': 'Отзыв не найден'}), 404
    row.approved = bool(data.get('approved'))
    db.session.commit()
    _audit_log('review.moderate', 'review', rid, {'approved': row.approved})
    return jsonify({'success': True, 'approved': row.approved})


@app.route('/admin/api/review/delete', methods=['POST'])
@admin_required
def admin_api_review_delete():
    """Удаление отзыва — с аудитом."""
    from models import Review
    data = request.get_json() or {}
    rid = data.get('id')
    if not rid:
        return jsonify({'success': False, 'error': 'id обязателен'}), 400
    row = db.session.get(Review, int(rid))
    if not row:
        return jsonify({'success': False, 'error': 'Отзыв не найден'}), 404
    db.session.delete(row)
    db.session.commit()
    _audit_log('review.delete', 'review', rid)
    return jsonify({'success': True})


# ---------- Чат клиент↔магазин (этап 7.3–7.4) ----------

@app.route('/admin/api/chats')
@admin_required
def admin_api_chats():
    """Список диалогов (сначала с непрочитанными, затем по последнему сообщению)."""
    from models import ChatDialog, ChatMessage
    dialogs = db.session.query(ChatDialog).all()
    items = []
    for d in dialogs:
        customer = db.session.get(Customer, d.customer_id)
        msgs = db.session.query(ChatMessage).filter_by(dialog_id=d.id)
        unread = msgs.filter_by(sender='client', read_by_admin=False).count()
        last = msgs.order_by(ChatMessage.id.desc()).first()
        items.append({
            'dialog': d.to_dict(),
            'customer': (customer.to_dict() if customer else
                         {'id': d.customer_id, 'name': '', 'phone': ''}),
            'unread': unread,
            'last_message': (last.text or '')[:120] if last else '',
            'last_at': last.created_at.isoformat(timespec='seconds') if last else '',
        })
    items.sort(key=lambda x: (x['unread'] > 0,
                              x['last_at'] or x['dialog']['updated_at']), reverse=True)
    return jsonify({'success': True, 'chats': items})


@app.route('/admin/api/chat/<int:customer_id>')
@admin_required
def admin_api_chat_history(customer_id):
    """История диалога с клиентом; клиентские сообщения помечаются прочитанными."""
    from models import ChatDialog, ChatMessage
    dialog = _own_dialog(customer_id)
    if dialog is None:
        return jsonify({'success': False, 'error': 'Диалог не найден'}), 404
    messages = db.session.query(ChatMessage).filter_by(
        dialog_id=dialog.id).order_by(ChatMessage.id.asc()).limit(500).all()
    changed = False
    for m in messages:
        if m.sender == 'client' and not m.read_by_admin:
            m.read_by_admin = True
            changed = True
    if changed:
        db.session.commit()
    customer = db.session.get(Customer, customer_id)
    return jsonify({'success': True, 'dialog': dialog.to_dict(),
                    'customer': customer.to_dict() if customer else None,
                    'messages': [m.to_dict() for m in messages]})


@app.route('/admin/api/chat/<int:customer_id>/reply', methods=['POST'])
@admin_required
def admin_api_chat_reply(customer_id):
    """Ответ админа клиенту — с аудитом."""
    from models import ChatDialog, ChatMessage
    data = request.get_json() or {}
    text = str(data.get('text') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'Введите сообщение'}), 400
    if len(text) > 2000:
        return jsonify({'success': False, 'error': 'Сообщение слишком длинное'}), 400
    dialog = _own_dialog(customer_id, create=True)
    if dialog.status != 'open':
        dialog.status = 'open'
    msg = ChatMessage(dialog_id=dialog.id, sender='admin', text=text,
                      read_by_admin=True, read_by_client=False)
    db.session.add(msg)
    db.session.commit()
    _audit_log('chat.reply', 'customer', customer_id,
               {'preview': text[:80]})
    return jsonify({'success': True, 'message': msg.to_dict()})


@app.route('/admin/api/chat/<int:customer_id>/status', methods=['POST'])
@admin_required
def admin_api_chat_status(customer_id):
    """Закрыть/открыть диалог — с аудитом."""
    from models import ChatDialog
    data = request.get_json() or {}
    status = data.get('status')
    if status not in ('open', 'closed'):
        return jsonify({'success': False, 'error': 'status: open|closed'}), 400
    dialog = _own_dialog(customer_id, create=True)
    dialog.status = status
    db.session.commit()
    _audit_log('chat.status', 'customer', customer_id, {'status': status})
    return jsonify({'success': True, 'status': status})


def _approved_reviews_html(limit=50):
    """HTML-лента одобренных отзывов для страницы «Отзывы» (новые сверху)."""
    from markupsafe import escape as esc
    from models import Review
    rows = (db.session.query(Review)
            .filter_by(approved=True)
            .order_by(Review.id.desc())
            .limit(limit).all())
    if not rows:
        return ''
    cards = []
    for r in rows:
        stars = '★' * (r.rating or 5) + '☆' * (5 - (r.rating or 5))
        text_html = '<br>'.join(esc(r.text).split('\n'))
        product = (f'<div class="wb-review-product">Товар: {esc(r.product_name)}</div>'
                   if r.product_name else '')
        date = r.created_at.strftime('%d.%m.%Y') if r.created_at else ''
        cards.append(
            '<div class="wb-review-card">'
            f'<div class="wb-review-head"><span class="wb-review-name">{esc(r.name)}</span>'
            f'<span class="wb-review-stars" title="Оценка: {r.rating or 5} из 5">{stars}</span>'
            f'<span class="wb-review-date">{date}</span></div>'
            f'{product}'
            f'<div class="wb-review-text">{text_html}</div>'
            '</div>')
    return ('<div class="wb-reviews-dynamic"><h2>Отзывы наших покупателей</h2>'
            + ''.join(cards) + '</div>')


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

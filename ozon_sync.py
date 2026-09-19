#!/usr/bin/env python3
"""
Синхронизация товаров с Ozon Seller API (OZ-1…OZ-6).

Использует официальный Seller API Ozon (https://api-seller.ozon.ru).
Для работы требуются Client-Id и Api-Key (раздел «API ключи» в кабинете
продавца, права на «Товары» и «Цены»).

Креденшелы берутся из переменных окружения OZON_CLIENT_ID / OZON_API_KEY
(приоритет) или из настроек сайта (site.ozon_client_id / site.ozon_api_key).

Демо-режима нет: без креденшелов синхронизация возвращает ошибку.
Хранилище — только БД (таблица products, source='ozon'); JSON-фолбэк
не пишется.
"""
import os
import re
from contextlib import nullcontext

import requests

API_BASE = 'https://api-seller.ozon.ru'
PAGE_LIMIT = 1000        # размер страницы/батча по документации Ozon
MAX_LIST_PAGES = 20      # страховка от бесконечного цикла пагинации


def app_context():
    """Контекст БД: внутри Flask-запроса — ничего не делать, иначе — standalone."""
    from flask import has_app_context
    if has_app_context():
        return nullcontext()
    from db import standalone_app
    return standalone_app().app_context()


def get_ozon_credentials():
    """(client_id, api_key): env имеет приоритет, иначе site_settings (OZ-1)."""
    client_id = os.environ.get('OZON_CLIENT_ID', '')
    api_key = os.environ.get('OZON_API_KEY', '')
    if client_id and api_key:
        return client_id, api_key
    try:
        import storage
        with app_context():
            site = storage.get_setting('site') or {}
        client_id = client_id or site.get('ozon_client_id', '') or ''
        api_key = api_key or site.get('ozon_api_key', '') or ''
    except Exception:
        pass
    return client_id, api_key


def _headers(client_id, api_key):
    return {
        'Client-Id': str(client_id),
        'Api-Key': api_key,
        'Content-Type': 'application/json',
    }


def _post(client_id, api_key, path, body):
    r = requests.post(f'{API_BASE}{path}', headers=_headers(client_id, api_key),
                      json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_product_ids(client_id, api_key):
    """Список product_id продавца: POST /v3/product/list (OZ-3, курсорная пагинация)."""
    ids = []
    last_id = ''
    for _ in range(MAX_LIST_PAGES):
        body = {
            'filter': {'offer_id': [], 'product_id': [], 'visibility': 'ALL'},
            'last_id': last_id,
            'limit': PAGE_LIMIT,
        }
        data = _post(client_id, api_key, '/v3/product/list', body)
        result = data.get('result') or {}
        batch = result.get('items') or []
        if not batch:
            break
        ids.extend(int(it['product_id']) for it in batch if it.get('product_id'))
        last_id = result.get('last_id') or ''
        if not last_id:
            break
    return ids


def fetch_product_info(client_id, api_key, product_ids):
    """Карточки товаров: POST /v3/product/info/list (OZ-3), батчами по 1000.

    Возвращает {product_id: item}, item — сырые поля ответа (id, name,
    offer_id, images, category, rating, reviews_amount).
    """
    info = {}
    for i in range(0, len(product_ids), PAGE_LIMIT):
        chunk = product_ids[i:i + PAGE_LIMIT]
        data = _post(client_id, api_key, '/v3/product/info/list',
                     {'product_id': chunk})
        for item in (data.get('result') or {}).get('items') or []:
            pid = item.get('id') or item.get('product_id')
            if pid:
                info[int(pid)] = item
    return info


def _to_rub(value):
    """Цена из ответа Ozon (строка/число в сотых) → int рублей."""
    try:
        return int(round(float(value or 0) / 100))
    except (TypeError, ValueError):
        return 0


def _price_block_to_prices(price_block):
    """Блок price ответа (цены в сотых) → (base, discount_price, mp_percent).

    base = old_price или price; discounted = marketing_price или price.
    Если скидки нет — discount_price=0, mp_percent=0. base=0 → None
    (товар пропускается, прежние цены сохраняются).
    """
    old = _to_rub(price_block.get('old_price'))
    current = _to_rub(price_block.get('price'))
    marketing = _to_rub(price_block.get('marketing_price'))
    base = old or current
    if not base:
        return None
    discounted = marketing or current
    if discounted and discounted < base:
        return base, discounted, round((1 - discounted / base) * 100)
    return base, 0, 0


def fetch_prices(client_id, api_key, product_ids):
    """Цены: POST /v5/product/info/prices (OZ-5).

    Возвращает {product_id: {'price': base, 'discount_price': ..., 'mp_discount_percent': ...}}.
    Товары с нулевой базовой ценой пропускаются.
    """
    prices = {}
    body = {
        'filter': {'offer_id': [], 'product_id': list(product_ids), 'visibility': 'ALL'},
        'last_id': '',
        'limit': PAGE_LIMIT,
    }
    data = _post(client_id, api_key, '/v5/product/info/prices', body)
    for item in (data.get('result') or {}).get('items') or []:
        pid = item.get('product_id')
        converted = _price_block_to_prices(item.get('price') or {})
        if pid and converted:
            base, discount_price, mp_percent = converted
            prices[int(pid)] = {
                'price': base,
                'discount_price': discount_price,
                'mp_discount_percent': mp_percent,
            }
    return prices


def fetch_stocks(client_id, api_key, product_ids):
    """Остатки: POST /v4/product/info/stocks (OZ-6).

    Возвращает {product_id: суммарный present}. Best-effort: при любой
    ошибке API возвращает {} (синк не должен падать из-за остатков).
    """
    stocks = {}
    body = {
        'filter': {'offer_id': [], 'product_id': list(product_ids), 'visibility': 'ALL'},
        'last_id': '',
        'limit': PAGE_LIMIT,
    }
    try:
        data = _post(client_id, api_key, '/v4/product/info/stocks', body)
        for item in (data.get('result') or {}).get('items') or []:
            pid = item.get('product_id')
            if pid:
                stocks[int(pid)] = stocks.get(int(pid), 0) + int(item.get('present') or 0)
    except requests.RequestException as e:
        import wb_sync
        wb_sync.log_sync('error', f'Остатки Ozon не загружены: {e}', source='ozon')
    except Exception as e:
        import wb_sync
        wb_sync.log_sync('error', f'Остатки Ozon не загружены: {e}', source='ozon')
    return stocks


def _slug(name):
    """Slug из названия: lowercase, не-alphanumeric → '-', схлопнуть повторы."""
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower())
    return re.sub(r'-+', '-', slug).strip('-')


def build_product(info, price_info):
    """Собирает единый объект товара (OZ-3) из карточки Ozon и ценового блока."""
    pid = int(info.get('id') or info.get('product_id') or 0)
    images = info.get('images') or []
    price = price_info.get('price', 0) or 0
    discount_price = price_info.get('discount_price', 0) or 0
    mp_percent = price_info.get('mp_discount_percent', 0) or 0
    return {
        'id': pid,
        'nm_id': pid,
        'source': 'ozon',
        'name': info.get('name', '') or '',
        'brand': '',
        'vendor_code': info.get('offer_id', '') or '',
        'category': info.get('category') or '',
        'price': price,
        'discount_price': discount_price,
        'wb_discount_percent': mp_percent,
        'admin_discount_percent': 0,
        'admin_discount_type': 'percent',
        'admin_discount_amount': 0,
        'photo': images[0] if images else '',
        'url': f'https://www.ozon.ru/product/{_slug(info.get("name", ""))}-{pid}/',
        'stock': 0,
        'updated_at': '',
    }


def _db_products_by_nm():
    """Существующие товары Ozon из БД: {nm_id: Product}."""
    from db import db
    from models import Product
    rows = db.session.query(Product).filter_by(source='ozon').all()
    return {p.nm_id: p for p in rows}


def _persist_products(items):
    """Сохранить товары Ozon в БД (upsert по (source='ozon', nm_id)).

    Перед записью проставляет visible по режиму выгрузки (OZ-4) и
    переносит скидки админа из существующих товаров (wb_sync.merge_with_existing
    работает по nm_id — источник не важен). JSON-фолбэк не пишется.
    """
    from db import db
    from models import Product
    import export_filter
    import wb_sync
    items = export_filter.apply_visibility(items, 'ozon')
    with app_context():
        existing = _db_products_by_nm()
        merged = wb_sync.merge_with_existing(existing, items)
        for item in merged:
            nm = int(item.get('nm_id') or 0)
            prod = existing.get(nm)
            if prod is None:
                prod = Product(source='ozon', nm_id=nm)
                db.session.add(prod)
            prod.name = item.get('name', '')
            prod.brand = item.get('brand', '')
            prod.vendor_code = item.get('vendor_code', '')
            prod.category = item.get('category', '')
            prod.price = item.get('price', 0) or 0
            prod.discount_price = item.get('discount_price', 0) or 0
            prod.mp_discount_percent = item.get('wb_discount_percent', 0) or 0
            prod.admin_discount_percent = item.get('admin_discount_percent', 0) or 0
            prod.admin_discount_type = item.get('admin_discount_type', 'percent')
            prod.admin_discount_amount = item.get('admin_discount_amount', 0) or 0
            prod.photo = item.get('photo', '')
            prod.url = item.get('url', '')
            prod.stock = item.get('stock', 0) or 0
            prod.visible = bool(item.get('visible', True))
            if item.get('rating'):
                prod.rating = item.get('rating')
            if item.get('review_count'):
                prod.review_count = item.get('review_count')
        db.session.commit()


def sync():
    """Полная синхронизация каталога Ozon (OZ-3…OZ-6): карточки + цены + остатки."""
    import wb_sync
    client_id, api_key = get_ozon_credentials()
    if not client_id or not api_key:
        wb_sync.log_sync('error', 'Ozon credentials не заданы', source='ozon')
        return {'success': False, 'error': 'Ozon credentials не заданы'}
    try:
        product_ids = fetch_product_ids(client_id, api_key)
        if not product_ids:
            wb_sync.log_sync('error', 'Ozon вернул пустой список товаров. Проверьте ключи.',
                             source='ozon')
            return {'success': False, 'error': 'Ozon вернул пустой список товаров. Проверьте ключи.'}

        info_by_id = fetch_product_info(client_id, api_key, product_ids)
        prices_by_id = fetch_prices(client_id, api_key, product_ids)

        new_items = []
        for pid in product_ids:
            info = info_by_id.get(pid)
            if not info:
                continue
            new_items.append(build_product(info, prices_by_id.get(pid, {})))

        # Остатки — best-effort: при ошибке API просто останутся 0
        stocks = fetch_stocks(client_id, api_key, product_ids)
        for p in new_items:
            p['stock'] = stocks.get(p['nm_id'], 0)

        _persist_products(new_items)
        wb_sync.log_sync('ok', 'Синхронизация с Ozon API завершена',
                         count=len(new_items), source='ozon')
        return {'success': True, 'count': len(new_items)}
    except requests.exceptions.RequestException as e:
        wb_sync.log_sync('error', f'Ошибка запроса к Ozon: {e}', source='ozon')
        return {'success': False, 'error': f'Ошибка запроса к Ozon: {e}'}
    except Exception as e:
        wb_sync.log_sync('error', str(e), source='ozon')
        return {'success': False, 'error': str(e)}


def sync_light():
    """Лёгкая синхронизация: только цены и остатки для уже загруженного каталога Ozon.

    Без запроса карточек (/v3/product/info/list) — для частого автообновления
    по расписанию. Товары, у которых в ответе цен нет (base=0), сохраняют
    прежние цены.
    """
    import wb_sync
    client_id, api_key = get_ozon_credentials()
    if not client_id or not api_key:
        wb_sync.log_sync('error', 'Ozon credentials не заданы', source='ozon')
        return {'success': False, 'error': 'Ozon credentials не заданы'}
    products = load_products()
    if not products:
        return {'success': False, 'error': 'Каталог пуст — сначала запустите полную синхронизацию'}
    product_ids = [p['nm_id'] for p in products]
    try:
        prices_by_id = fetch_prices(client_id, api_key, product_ids)
        stocks = fetch_stocks(client_id, api_key, product_ids)
        for p in products:
            info = prices_by_id.get(p['nm_id'])
            if info:
                p['price'] = info['price']
                p['discount_price'] = info['discount_price']
                p['wb_discount_percent'] = info['mp_discount_percent']
            if p['nm_id'] in stocks:
                p['stock'] = stocks[p['nm_id']]
        _persist_products(products)
        wb_sync.log_sync('ok', 'Обновление цен и остатков Ozon завершено',
                         count=len(products), source='ozon')
        return {'success': True, 'count': len(products), 'light': True}
    except Exception as e:
        wb_sync.log_sync('error', f'Ошибка обновления цен/остатков Ozon: {e}', source='ozon')
        return {'success': False, 'error': str(e)}


def load_products():
    """Товары Ozon из БД: [Product.to_product_dict()], source='ozon'."""
    from db import db
    from models import Product
    with app_context():
        rows = (db.session.query(Product)
                .filter_by(source='ozon')
                .order_by(Product.id).all())
        return [p.to_product_dict() for p in rows]


if __name__ == '__main__':
    import sys
    result = sync()
    print(result)
    sys.exit(0 if result.get('success') else 1)

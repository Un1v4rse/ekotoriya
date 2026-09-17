#!/usr/bin/env python3
"""
Синхронизация товаров с Wildberries.

Использует официальный Seller API WB (https://api-seller.wildberries.ru).
Для работы требуется API-токен с категорией "Контент" или "Цены и скидки".

Токен берётся из переменной окружения WB_API_TOKEN или из
site.wb_api_token в data/content.json.

Если токен не задан, работает демо-режим: создаёт несколько
заглушек-товаров, чтобы можно было сразу проверить UI.
"""
import json
import os
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'content.json'
PRODUCTS_FILE = BASE_DIR / 'data' / 'wb_products.json'
SYNC_LOG_FILE = BASE_DIR / 'data' / 'sync_log.json'

API_BASE = 'https://api-seller.wildberries.ru'

# Демо-товары (nmID Экотории, найденные через поиск)
DEMO_PRODUCTS = [
    {'nm_id': 890129778, 'name': 'Непромокаемый наматрасник 200х200 на молнии высота 17-21 см Экотория', 'price': 3990, 'discount_price': 3591, 'photo': 'https://basket-01.wb.ru/vol890/part890/890129778/images/big/1.jpg'},
    {'nm_id': 67928567, 'name': 'Наматрасник 160х200 на молнии высота 21-23 см чехол стеганый Экотория', 'price': 2890, 'discount_price': 2601, 'photo': 'https://basket-01.wb.ru/vol67/part679/67928567/images/big/1.jpg'},
    {'nm_id': 381073941, 'name': 'Наматрасник 140х200 на молнии чехол стеганый Экотория', 'price': 2490, 'discount_price': 2241, 'photo': 'https://basket-04.wb.ru/vol381/part381/381073941/images/big/1.jpg'},
    {'nm_id': 863971369, 'name': 'Наматрасник 200х200 на резинках чехол стеганый Экотория', 'price': 2790, 'discount_price': 2511, 'photo': 'https://basket-09.wb.ru/vol863/part863/863971369/images/big/1.jpg'},
    {'nm_id': 182356719, 'name': 'Наматрасник 180х200 на молнии высота 21-23 см чехол стеганый Экотория', 'price': 3290, 'discount_price': 2961, 'photo': 'https://basket-02.wb.ru/vol182/part182/182356719/images/big/1.jpg'},
    {'nm_id': 47521588, 'name': 'Наматрасник 140х200 на молнии высота 17-20 см чехол стеганый Экотория', 'price': 2190, 'discount_price': 1971, 'photo': 'https://basket-05.wb.ru/vol475/part475/47521588/images/big/1.jpg'},
    {'nm_id': 820392828, 'name': 'Наматрасник 140х200 на резинках чехол стеганый 4D-Fix Экотория', 'price': 2290, 'discount_price': 2061, 'photo': 'https://basket-10.wb.ru/vol820/part820/820392828/images/big/1.jpg'},
    {'nm_id': 1399155441, 'name': 'Наматрасник 160x200х20 см непромокаемый на молнии Экотория', 'price': 3690, 'discount_price': 3321, 'photo': 'https://basket-13.wb.ru/vol1399/part1399/1399155441/images/big/1.jpg'},
]


def load_json(path):
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_sync(status, message, count=None):
    """Дописывает запись в журнал синхронизации (data/sync_log.json, последние 200)."""
    entries = load_json(SYNC_LOG_FILE).get('entries', [])
    from datetime import datetime
    entries.append({
        'time': datetime.now().isoformat(timespec='seconds'),
        'status': status,
        'message': message,
        'count': count,
    })
    save_json(SYNC_LOG_FILE, {'entries': entries[-200:]})


def get_sync_log(limit=50):
    """Последние записи журнала синхронизации."""
    entries = load_json(SYNC_LOG_FILE).get('entries', [])
    return entries[-limit:][::-1]


def get_wb_token():
    token = os.environ.get('WB_API_TOKEN')
    if token:
        return token
    content = load_json(DATA_FILE)
    return content.get('site', {}).get('wb_api_token', '')


def build_product(card, price_info):
    """Собирает единый объект товара из карточки WB и ценового блока."""
    photos = card.get('photos', [])
    photo = ''
    if photos:
        photo = photos[0].get('c516x688') or photos[0].get('big') or photos[0].get('square') or ''

    sizes = price_info.get('sizes', [])
    size = sizes[0] if sizes else {}
    price = size.get('price') or price_info.get('price') or 0
    discount_price = size.get('discountedPrice') or price_info.get('discountedPrice') or 0
    wb_discount = price_info.get('discount') or 0

    nm_id = card.get('nmID') or price_info.get('nmID') or 0

    return {
        'id': nm_id,
        'nm_id': nm_id,
        'name': card.get('title', ''),
        'brand': card.get('brand', ''),
        'vendor_code': card.get('vendorCode', ''),
        'price': price,
        'discount_price': discount_price,
        'wb_discount_percent': wb_discount,
        'admin_discount_percent': 0,
        'photo': photo,
        'url': f'https://www.wildberries.ru/catalog/{nm_id}/detail.aspx',
        'updated_at': card.get('updatedAt', ''),
    }


def fetch_cards(token, limit=100, max_total=1000):
    """Список карточек продавца через Content API (с курсорной постраничностью)."""
    url = f'{API_BASE}/content/v2/get/cards/list?locale=ru'
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    cards = []
    cursor = None
    while len(cards) < max_total:
        body = {
            'settings': {
                'sort': {'ascending': True},
                'cursor': {'limit': limit},
                'filter': {'withPhoto': -1},
            }
        }
        if cursor:
            body['settings']['cursor']['updatedAt'] = cursor.get('updatedAt', '')
            body['settings']['cursor']['nmID'] = cursor.get('nmID', 0)
        r = requests.post(url, headers=headers, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        batch = data.get('cards', [])
        if not batch:
            break
        cards.extend(batch)
        cursor = data.get('cursor') or {}
        # Курсор пустой/совпадает — дальше выгружать нечего
        if not cursor.get('updatedAt') or len(batch) < limit:
            break
    return cards[:max_total]


def _normalize_price_item(p):
    """Привести запись цены (новый или старый формат WB) к единому виду."""
    price = p.get('price', 0)
    discounted = p.get('discountedPrice') or p.get('discountPrice') or 0
    return {
        'nmID': p.get('nmID', 0),
        'price': price,
        'discountedPrice': discounted,
        'discount': p.get('discount', 0),
        'sizes': [{'price': price, 'discountedPrice': discounted}],
    }


def fetch_prices(token, nm_ids):
    """Цены и скидки через официальный API «Цены и скидки» WB.

    Основной метод: POST /public/api/v1/prices. Если он недоступен —
    резервно используется старый метод /api/v2/list/goods/filter.
    """
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    try:
        r = requests.post(
            f'{API_BASE}/public/api/v1/prices',
            headers=headers, json={'nmIDs': nm_ids}, timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            items = data if isinstance(data, list) else data.get('data', {}).get('listGoods', [])
            return [_normalize_price_item(p) for p in items]
    except requests.RequestException:
        pass
    # Резервный (старый) метод
    r = requests.post(f'{API_BASE}/api/v2/list/goods/filter', headers=headers, json={'nmList': nm_ids}, timeout=60)
    if r.status_code == 200:
        return r.json().get('data', {}).get('listGoods', [])
    return []


def fetch_warehouses(token):
    """Список складов продавца: GET /api/v3/warehouses → [warehouseId, ...]."""
    r = requests.get(
        f'{API_BASE}/api/v3/warehouses',
        headers={'Authorization': token}, timeout=60,
    )
    r.raise_for_status()
    return [w.get('id') for w in r.json() if w.get('id')]


def fetch_stocks(token, nm_ids):
    """Остатки товаров: POST /api/v3/stocks/{warehouseId} для каждого склада.

    Возвращает {nm_id: суммарный остаток по всем складам}. Best-effort:
    при любой ошибке API возвращает {} (каталог не должен падать из-за
    отсутствия прав на «Маркетплейс» — токен «Контент» складов не видит).
    """
    stocks = {}
    skus = [str(n) for n in nm_ids]
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    try:
        for warehouse_id in fetch_warehouses(token):
            r = requests.post(
                f'{API_BASE}/api/v3/stocks/{warehouse_id}',
                headers=headers, json={'skus': skus}, timeout=60,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get('stocks', []) if isinstance(data, dict) else data
            for item in items or []:
                nm = int(item.get('sku', 0) or 0)
                if nm:
                    stocks[nm] = stocks.get(nm, 0) + int(item.get('amount', 0) or 0)
    except requests.RequestException as e:
        log_sync('error', f'Остатки WB не загружены: {e}')
    except Exception as e:
        log_sync('error', f'Остатки WB не загружены: {e}')
    return stocks


def push_discount(token, nm_id, percent):
    """Отправляет скидку в WB через API «Цены и скидки».

    Пробуем новый метод /discount/v1/save, при 404/405 — старый
    /api/v2/list/goods/set/discounts. Возвращает (ok, message).
    """
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    body = [{'nmID': int(nm_id), 'discount': int(percent)}]
    try:
        r = requests.post(f'{API_BASE}/discount/v1/save', headers=headers, json=body, timeout=60)
        if r.status_code in (404, 405):
            r = requests.post(f'{API_BASE}/api/v2/list/goods/set/discounts', headers=headers, json=body, timeout=60)
        if r.status_code in (200, 201, 204):
            return True, 'Скидка отправлена в WB'
        return False, f'WB API вернул {r.status_code}: {r.text[:200]}'
    except requests.RequestException as e:
        return False, f'Ошибка запроса к WB: {e}'
    except Exception as e:
        return False, str(e)


def merge_with_existing(existing, new_items):
    existing_by_nm = {p['nm_id']: p for p in existing}
    merged = []
    for item in new_items:
        nm = item['nm_id']
        old = existing_by_nm.get(nm, {})
        item['admin_discount_percent'] = old.get('admin_discount_percent', 0)
        merged.append(item)
    return merged


def sync(token=None, demo=False):
    if demo or not token:
        items = []
        for p in DEMO_PRODUCTS:
            items.append({
                'id': p['nm_id'],
                'nm_id': p['nm_id'],
                'name': p['name'],
                'brand': 'Экотория',
                'vendor_code': '',
                'price': p['price'],
                'discount_price': p['discount_price'],
                'wb_discount_percent': 10,
                'admin_discount_percent': 0,
                'photo': p['photo'],
                'url': f'https://www.wildberries.ru/catalog/{p["nm_id"]}/detail.aspx',
                'updated_at': '',
                'stock': 10,
            })
        save_json(PRODUCTS_FILE, {'products': items, 'demo': True})
        log_sync('ok', 'Демо-синхронизация завершена', count=len(items))
        return {'success': True, 'count': len(items), 'demo': True}

    try:
        cards = fetch_cards(token)
        if not cards:
            log_sync('error', 'WB вернул пустой список карточек. Проверьте токен.')
            return {'success': False, 'error': 'WB вернул пустой список карточек. Проверьте токен.'}

        nm_ids = [c['nmID'] for c in cards]
        price_list = fetch_prices(token, nm_ids)
        prices_by_nm = {p['nmID']: p for p in price_list}

        new_items = [build_product(c, prices_by_nm.get(c['nmID'], {})) for c in cards]

        existing = load_json(PRODUCTS_FILE).get('products', [])
        merged = merge_with_existing(existing, new_items)

        # Остатки — best-effort: без прав на «Маркетплейс» просто останутся 0
        stocks = fetch_stocks(token, nm_ids)
        for p in merged:
            p['stock'] = stocks.get(p['nm_id'], 0)

        save_json(PRODUCTS_FILE, {'products': merged, 'demo': False})
        log_sync('ok', f'Синхронизация с WB API завершена', count=len(merged))
        return {'success': True, 'count': len(merged), 'demo': False}
    except requests.exceptions.RequestException as e:
        log_sync('error', f'Ошибка запроса к WB: {e}')
        return {'success': False, 'error': f'Ошибка запроса к WB: {e}'}
    except Exception as e:
        log_sync('error', str(e))
        return {'success': False, 'error': str(e)}


def sync_light(token):
    """Лёгкая синхронизация: только цены и остатки для уже загруженного каталога.

    Без запроса карточек (Content API) — для частого автообновления по расписанию.
    """
    if not token:
        return {'success': False, 'error': 'WB_API_TOKEN не задан'}
    data = load_json(PRODUCTS_FILE)
    products = data.get('products', [])
    if not products:
        return {'success': False, 'error': 'Каталог пуст — сначала запустите полную синхронизацию'}
    nm_ids = [p['nm_id'] for p in products]
    try:
        price_list = fetch_prices(token, nm_ids)
        prices_by_nm = {p['nmID']: p for p in price_list}
        stocks = fetch_stocks(token, nm_ids)
        for p in products:
            info = prices_by_nm.get(p['nm_id'], {})
            size = (info.get('sizes') or [{}])[0]
            if size.get('price') or info.get('price'):
                p['price'] = size.get('price') or info.get('price')
            if size.get('discountedPrice') or info.get('discountedPrice'):
                p['discount_price'] = size.get('discountedPrice') or info.get('discountedPrice')
            if info.get('discount'):
                p['wb_discount_percent'] = info['discount']
            if p['nm_id'] in stocks:
                p['stock'] = stocks[p['nm_id']]
        save_json(PRODUCTS_FILE, {'products': products, 'demo': data.get('demo', False)})
        log_sync('ok', 'Обновление цен и остатков завершено', count=len(products))
        return {'success': True, 'count': len(products), 'light': True}
    except Exception as e:
        log_sync('error', f'Ошибка обновления цен/остатков: {e}')
        return {'success': False, 'error': str(e)}


def load_products():
    products = load_json(PRODUCTS_FILE).get('products', [])
    for p in products:
        if 'id' not in p:
            p['id'] = p.get('nm_id', 0)
    return products


def set_admin_discount(nm_id, percent):
    data = load_json(PRODUCTS_FILE)
    for p in data.get('products', []):
        if p['nm_id'] == nm_id:
            p['admin_discount_percent'] = percent
            break
    save_json(PRODUCTS_FILE, data)


def final_price(product):
    """Цена с учётом скидки WB и дополнительной скидки из админки."""
    base = product.get('price', 0)
    if not base:
        return 0
    wb_discount = product.get('wb_discount_percent', 0)
    admin_discount = product.get('admin_discount_percent', 0)
    total_discount = min(wb_discount + admin_discount, 99)
    return round(base * (1 - total_discount / 100))


if __name__ == '__main__':
    import sys
    t = get_wb_token()
    if not t:
        print('WB_API_TOKEN не задан, используется демо-режим.')
        print(sync(demo=True))
        sys.exit(0)
    print(sync(token=t))

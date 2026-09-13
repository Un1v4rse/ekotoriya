#!/usr/bin/env python3
"""
Синхронизация товаров с Ozon.

Использует официальный Seller API Ozon (https://api-seller.ozon.ru).
Для работы требуется Client-ID и Api-Key из личного кабинета продавца.

Токены берутся из переменных окружения OZON_CLIENT_ID / OZON_API_KEY
или из site.ozon_client_id / site.ozon_api_key в data/content.json.

Если токены не заданы, работает демо-режим.
"""
import json
import os
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'content.json'
PRODUCTS_FILE = BASE_DIR / 'data' / 'ozon_products.json'

API_BASE = 'https://api-seller.ozon.ru'

DEMO_PRODUCTS = [
    {'offer_id': 'nam-001', 'name': 'Непромокаемый наматрасник 200х200 на молнии высота 17-21 см Экотория', 'price': 3990, 'discount_price': 3591, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-002', 'name': 'Наматрасник 160х200 на молнии высота 21-23 см чехол стеганый Экотория', 'price': 2890, 'discount_price': 2601, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-003', 'name': 'Наматрасник 140х200 на молнии чехол стеганый Экотория', 'price': 2490, 'discount_price': 2241, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-004', 'name': 'Наматрасник 200х200 на резинках чехол стеганый Экотория', 'price': 2790, 'discount_price': 2511, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-005', 'name': 'Наматрасник 180х200 на молнии высота 21-23 см чехол стеганый Экотория', 'price': 3290, 'discount_price': 2961, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-006', 'name': 'Наматрасник 140х200 на молнии высота 17-20 см чехол стеганый Экотория', 'price': 2190, 'discount_price': 1971, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-007', 'name': 'Наматрасник 140х200 на резинках чехол стеганый 4D-Fix Экотория', 'price': 2290, 'discount_price': 2061, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
    {'offer_id': 'nam-008', 'name': 'Наматрасник 160x200х20 см непромокаемый на молнии Экотория', 'price': 3690, 'discount_price': 3321, 'photo': 'https://cdn1.ozone.ru/s3/cm-goods-service/placeholder.jpg'},
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


def get_ozon_credentials():
    client_id = os.environ.get('OZON_CLIENT_ID') or ''
    api_key = os.environ.get('OZON_API_KEY') or ''
    if not client_id or not api_key:
        content = load_json(DATA_FILE)
        client_id = content.get('site', {}).get('ozon_client_id', '')
        api_key = content.get('site', {}).get('ozon_api_key', '')
    return client_id, api_key


def build_product(item, price_info):
    offer_id = item.get('offer_id', '')
    name = item.get('name', '') or item.get('product_name', '')
    images = item.get('images', [])
    photo = images[0] if images else item.get('primary_image', '')
    price = price_info.get('price', item.get('price', 0))
    discount_price = price_info.get('price', item.get('price', 0))
    ozon_discount = 0
    if discount_price and price and discount_price < price:
        ozon_discount = round((price - discount_price) / price * 100)
    return {
        'id': offer_id,
        'offer_id': offer_id,
        'name': name,
        'brand': item.get('brand', ''),
        'price': price,
        'discount_price': discount_price,
        'ozon_discount_percent': ozon_discount,
        'admin_discount_percent': 0,
        'photo': photo,
        'url': f'https://www.ozon.ru/product/{offer_id}',
        'updated_at': '',
    }


def fetch_product_list(client_id, api_key, limit=100):
    url = f'{API_BASE}/v3/product/list'
    headers = {'Client-Id': client_id, 'Api-Key': api_key, 'Content-Type': 'application/json'}
    body = {'filter': {'offer_id': '', 'product_id': '', 'visibility': 'ALL'}, 'limit': limit, 'page': 1}
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    return r.json().get('items', [])


def fetch_prices(client_id, api_key, offer_ids):
    url = f'{API_BASE}/v4/product/info/prices'
    headers = {'Client-Id': client_id, 'Api-Key': api_key, 'Content-Type': 'application/json'}
    body = {'offer_id': offer_ids}
    r = requests.post(url, headers=headers, json=body, timeout=60)
    if r.status_code == 200:
        return r.json().get('items', [])
    return []


def merge_with_existing(existing, new_items):
    existing_by_id = {p['offer_id']: p for p in existing}
    merged = []
    for item in new_items:
        oid = item['offer_id']
        old = existing_by_id.get(oid, {})
        item['admin_discount_percent'] = old.get('admin_discount_percent', 0)
        merged.append(item)
    return merged


def sync(client_id=None, api_key=None, demo=False):
    if demo or not client_id or not api_key:
        items = []
        for p in DEMO_PRODUCTS:
            items.append({
                'id': p['offer_id'],
                'offer_id': p['offer_id'],
                'name': p['name'],
                'brand': 'Экотория',
                'price': p['price'],
                'discount_price': p['discount_price'],
                'ozon_discount_percent': 10,
                'admin_discount_percent': 0,
                'photo': p['photo'],
                'url': f'https://www.ozon.ru/product/{p["offer_id"]}',
                'updated_at': '',
            })
        save_json(PRODUCTS_FILE, {'products': items, 'demo': True})
        return {'success': True, 'count': len(items), 'demo': True}

    try:
        product_list = fetch_product_list(client_id, api_key)
        if not product_list:
            return {'success': False, 'error': 'Ozon вернул пустой список товаров. Проверьте Client-ID и Api-Key.'}
        offer_ids = [p['offer_id'] for p in product_list if p.get('offer_id')]
        price_list = fetch_prices(client_id, api_key, offer_ids) if offer_ids else []
        prices_by_offer = {p['offer_id']: p for p in price_list}
        new_items = [build_product(p, prices_by_offer.get(p.get('offer_id', ''), {})) for p in product_list]
        existing = load_json(PRODUCTS_FILE).get('products', [])
        merged = merge_with_existing(existing, new_items)
        save_json(PRODUCTS_FILE, {'products': merged, 'demo': False})
        return {'success': True, 'count': len(merged), 'demo': False}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': f'Ошибка запроса к Ozon: {e}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def load_products():
    products = load_json(PRODUCTS_FILE).get('products', [])
    for p in products:
        if 'id' not in p:
            p['id'] = p.get('offer_id', '')
    return products


def set_admin_discount(offer_id, percent):
    data = load_json(PRODUCTS_FILE)
    for p in data.get('products', []):
        if p['offer_id'] == offer_id:
            p['admin_discount_percent'] = percent
            break
    save_json(PRODUCTS_FILE, data)


def final_price(product):
    base = product.get('price', 0)
    if not base:
        return 0
    ozon_discount = product.get('ozon_discount_percent', 0)
    admin_discount = product.get('admin_discount_percent', 0)
    total_discount = min(ozon_discount + admin_discount, 99)
    return round(base * (1 - total_discount / 100))


if __name__ == '__main__':
    import sys
    cid, key = get_ozon_credentials()
    if not cid or not key:
        print('OZON_CLIENT_ID / OZON_API_KEY не заданы, используется демо-режим.')
        print(sync(demo=True))
        sys.exit(0)
    print(sync(client_id=cid, api_key=key))

#!/usr/bin/env python3
"""
Простая база клиентов и заявок.

Хранит данные в data/customers.json.
При регистрации или оформлении заявки на товар клиент добавляется
(или обновляется) в базу, а заявка записывается отдельно.
"""
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'customers.json'


def load_data():
    if not DATA_FILE.exists():
        return {'customers': [], 'orders': []}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_customer(name, phone, email=''):
    """Add or update a customer by phone."""
    data = load_data()
    phone_norm = ''.join(c for c in phone if c.isdigit() or c == '+').strip()
    customer = next((c for c in data['customers'] if c['phone'] == phone_norm), None)
    now = datetime.now().isoformat()
    if customer:
        customer['name'] = name or customer.get('name', '')
        customer['email'] = email or customer.get('email', '')
        customer['updated_at'] = now
    else:
        customer = {
            'id': len(data['customers']) + 1,
            'name': name,
            'phone': phone_norm,
            'email': email,
            'created_at': now,
            'updated_at': now,
        }
        data['customers'].append(customer)
    save_data(data)
    return customer


def _norm_product_id(pid):
    if pid is None:
        return 0
    if isinstance(pid, int):
        return pid
    if isinstance(pid, str) and pid.isdigit():
        return int(pid)
    return str(pid)


def add_order(name, phone, email, product_id, product_name, price):
    """Register a new order and ensure customer exists."""
    customer = add_customer(name, phone, email)
    data = load_data()
    order = {
        'id': len(data['orders']) + 1,
        'customer_id': customer['id'],
        'name': name,
        'phone': customer['phone'],
        'email': email,
        'product_id': _norm_product_id(product_id),
        'product_name': product_name,
        'price': price,
        'status': 'new',
        'created_at': datetime.now().isoformat(),
    }
    data['orders'].append(order)
    save_data(data)
    return order


def get_customers():
    return load_data()['customers']


def get_orders():
    return load_data()['orders']


def update_order_status(order_id, status):
    data = load_data()
    for o in data['orders']:
        if o['id'] == order_id:
            o['status'] = status
            save_data(data)
            return o
    return None


def add_like(product_id, product_name=''):
    """Register a public like for a product."""
    data = load_data()
    if 'likes' not in data:
        data['likes'] = []
    data['likes'].append({
        'product_id': _norm_product_id(product_id),
        'product_name': product_name,
        'created_at': datetime.now().isoformat(),
    })
    save_data(data)
    return True


def get_like_counts():
    """Return dict {product_id: count} from likes."""
    data = load_data()
    counts = {}
    for like in data.get('likes', []):
        pid = like.get('product_id') or like.get('nm_id', 0)
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _product_key(p):
    """Return universal product id from a product dict."""
    return p.get('nm_id') or p.get('offer_id') or p.get('id') or 0


def get_popular_products(products, limit=8, like_weight=1):
    """Return top products by orders + likes frequency from customer base.

    products — list of product dicts (WB or Ozon).
    like_weight — how many orders one like counts as.
    """
    orders = get_orders()
    like_counts = get_like_counts()
    order_counts = {}
    for o in orders:
        pid = o.get('product_id') or o.get('nm_id', 0)
        if pid:
            order_counts[pid] = order_counts.get(pid, 0) + 1

    scored = []
    for p in products:
        pid = _product_key(p)
        if not pid:
            continue
        score = order_counts.get(pid, 0) + like_counts.get(pid, 0) * like_weight
        if score > 0:
            scored.append({'product': p, 'score': score, 'orders': order_counts.get(pid, 0), 'likes': like_counts.get(pid, 0)})

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:limit]

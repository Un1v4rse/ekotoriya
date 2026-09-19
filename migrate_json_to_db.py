#!/usr/bin/env python3
"""
Одноразовая (но идемпотентная) миграция JSON-файлов в БД (AR-2, задача 1.5).

Переносит:
    data/content.json    → pages (32 страницы, blocks пересчитывается в content_html
                           через blocks.render_blocks) + site_settings (site/menu/socials)
    data/wb_products.json → products (source='wb', visible=True)
    data/customers.json   → customers, orders, events (лайки → type='like')
    data/sync_log.json    → sync_logs

Идемпотентность: повторный запуск не плодит дубли (upsert по slug / (source, nm_id) /
phone / order id / like с тем же created_at).

Запуск:  python migrate_json_to_db.py
"""
import json
from pathlib import Path

from db import db, standalone_app
from models import Customer, Event, Order, Page, Product, SiteSetting, SyncLog
import blocks as blocks_module

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'


def load_json(name, default):
    path = DATA_DIR / name
    if not path.exists():
        return default
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def migrate_settings(content):
    """site/menu/socials → site_settings (upsert по key)."""
    for key in ('site', 'menu', 'socials'):
        value = content.get(key)
        if value is None:
            continue
        row = db.session.get(SiteSetting, key)
        if row is None:
            db.session.add(SiteSetting(key=key, value=value))
        else:
            row.value = value
    print(f'  site_settings: site/menu/socials сохранены')


def migrate_pages(content):
    pages = content.get('pages', [])
    existing = {p.slug: p for p in db.session.query(Page).all()}
    added = updated = 0
    for pdata in pages:
        slug = pdata.get('slug')
        if not slug:
            continue
        page = existing.get(slug)
        is_new = page is None
        if is_new:
            page = Page(slug=slug)
            db.session.add(page)
        page.url = pdata.get('url', '/')
        page.title = pdata.get('title', '')
        page.h1 = pdata.get('h1', '')
        page.meta_description = pdata.get('meta_description', '')
        page.meta_keywords = pdata.get('meta_keywords', '')
        page.canonical = pdata.get('canonical', '')
        page.template = pdata.get('template', '')
        if isinstance(pdata.get('blocks'), list):
            page.blocks = pdata['blocks']
            page.content_html = blocks_module.render_blocks(pdata['blocks'])
        else:
            page.blocks = None
            page.content_html = pdata.get('content_html', '')
        added += 1 if is_new else 0
        updated += 0 if is_new else 1
    db.session.commit()
    print(f'  pages: всего {len(pages)} (новых {added}, обновлено {updated})')


def migrate_products():
    data = load_json('wb_products.json', {})
    items = data.get('products', [])
    existing = {(p.source, p.nm_id): p for p in db.session.query(Product).all()}
    added = updated = 0
    for item in items:
        nm_id = int(item.get('nm_id') or item.get('id') or 0)
        if not nm_id:
            continue
        key = ('wb', nm_id)
        prod = existing.get(key)
        is_new = prod is None
        if is_new:
            prod = Product(source='wb', nm_id=nm_id)
            db.session.add(prod)
        prod.name = item.get('name', '')
        prod.brand = item.get('brand', '')
        prod.vendor_code = item.get('vendor_code', '')
        prod.category = item.get('category', '')
        prod.price = item.get('price', 0) or 0
        prod.discount_price = item.get('discount_price', 0) or 0
        prod.mp_discount_percent = item.get('wb_discount_percent', 0) or 0
        prod.admin_discount_percent = item.get('admin_discount_percent', 0) or 0
        prod.photo = item.get('photo', '')
        prod.url = item.get('url', '')
        prod.stock = item.get('stock', 0) or 0
        prod.visible = True
        if item.get('rating') or item.get('review_rating'):
            try:
                prod.rating = float(item.get('rating') or item.get('review_rating'))
            except (TypeError, ValueError):
                pass
        if item.get('review_count'):
            try:
                prod.review_count = int(item.get('review_count'))
            except (TypeError, ValueError):
                pass
        added += 1 if is_new else 0
        updated += 0 if is_new else 1
    db.session.commit()
    print(f'  products (wb): всего {len(items)} (новых {added}, обновлено {updated})')


def migrate_customers():
    data = load_json('customers.json', {'customers': [], 'orders': [], 'likes': []})
    by_phone = {c.phone: c for c in db.session.query(Customer).all()}

    for cdata in data.get('customers', []):
        phone = cdata.get('phone', '')
        cust = by_phone.get(phone)
        if cust is None:
            cust = Customer(phone=phone)
            db.session.add(cust)
            by_phone[phone] = cust
        cust.name = cdata.get('name', '')
        cust.email = cdata.get('email', '')
    db.session.commit()
    print(f'  customers: {len(data.get("customers", []))}')

    cust_by_id = {}
    for c in db.session.query(Customer).all():
        cust_by_id[c.phone] = c.id
    existing_orders = {o.id for o in db.session.query(Order).all()}
    added_orders = 0
    for odata in data.get('orders', []):
        oid = odata.get('id')
        if oid in existing_orders:
            continue
        phone = odata.get('phone', '')
        customer_id = cust_by_id.get(phone)
        if customer_id is None:
            cust = by_phone.get(phone)
            if cust is None:
                cust = Customer(phone=phone, name=odata.get('name', ''))
                db.session.add(cust)
                db.session.flush()
                by_phone[phone] = cust
            customer_id = cust.id
        order = Order(
            id=oid,
            customer_id=customer_id,
            name=odata.get('name', ''),
            phone=phone,
            email=odata.get('email', ''),
            product_id=str(odata.get('product_id', '') or ''),
            product_name=odata.get('product_name', ''),
            price=odata.get('price', 0) or 0,
            status=odata.get('status', 'new'),
            source='oneclick',
        )
        db.session.add(order)
        added_orders += 1
    db.session.commit()
    print(f'  orders: добавлено {added_orders} (всего в JSON {len(data.get("orders", []))})')

    # Лайки → events (type='like'), дедупликация по (type, product_id, created_at)
    existing_likes = {
        (e.product_id, e.created_at)
        for e in db.session.query(Event).filter_by(type='like').all()
    }
    added_likes = 0
    for like in data.get('likes', []):
        from datetime import datetime
        created = like.get('created_at')
        try:
            created_at = datetime.fromisoformat(created) if created else None
        except ValueError:
            created_at = None
        pid = str(like.get('product_id') or like.get('nm_id') or '')
        if not pid:
            continue
        if created_at is not None and (pid, created_at) in existing_likes:
            continue
        db.session.add(Event(
            type='like',
            product_id=pid,
            product_name=like.get('product_name', ''),
            created_at=created_at,
        ))
        added_likes += 1
    db.session.commit()
    print(f'  likes → events: добавлено {added_likes} (всего в JSON {len(data.get("likes", []))})')


def migrate_sync_log():
    data = load_json('sync_log.json', {})
    entries = data.get('entries', [])
    if not entries:
        print('  sync_logs: пусто')
        return
    from datetime import datetime
    existing = {
        (e.source, e.created_at, e.message)
        for e in db.session.query(SyncLog).all()
    }
    added = 0
    for entry in entries:
        try:
            created_at = datetime.fromisoformat(entry.get('time', ''))
        except ValueError:
            created_at = None
        key = (entry.get('source', 'wb'), created_at, entry.get('message', ''))
        if key in existing:
            continue
        db.session.add(SyncLog(
            source=entry.get('source', 'wb'),
            status=entry.get('status', 'ok'),
            message=entry.get('message', ''),
            count=entry.get('count'),
            created_at=created_at,
        ))
        added += 1
    db.session.commit()
    print(f'  sync_logs: добавлено {added} (всего в JSON {len(entries)})')


def verify():
    counts = {
        'pages': db.session.query(Page).count(),
        'products': db.session.query(Product).count(),
        'customers': db.session.query(Customer).count(),
        'orders': db.session.query(Order).count(),
        'events': db.session.query(Event).count(),
        'sync_logs': db.session.query(SyncLog).count(),
    }
    print('Сверка записей в БД:', counts)
    return counts


def main():
    app = standalone_app()
    with app.app_context():
        content = load_json('content.json', {'pages': [], 'site': {}, 'menu': [], 'socials': []})
        print('Миграция content.json → БД:')
        migrate_settings(content)
        migrate_pages(content)
        print('Миграция wb_products.json → БД:')
        migrate_products()
        print('Миграция customers.json → БД:')
        migrate_customers()
        print('Миграция sync_log.json → БД:')
        migrate_sync_log()
        print()
        verify()


if __name__ == '__main__':
    main()

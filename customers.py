#!/usr/bin/env python3
"""
База клиентов, заявок и лайков на БД (AR-1, CL-1…CL-3).

Публичные функции и формат возвращаемых dict сохранены (app.py и шаблоны
не меняются):
    add_customer(name, phone, email) -> customer dict
    add_order(name, phone, email, product_id, product_name, price) -> order dict
    get_customers() / get_orders() -> list of dict
    update_order_status(order_id, status) -> order dict | None
    add_like(product_id, product_name)
    get_like_counts() -> {product_id: count}
    get_popular_products(products, limit, like_weight)

Дедупликация клиентов — по нормализованному телефону (CL-2; email —
дополнительный признак при совпадении). Лайки хранятся в events (RS-1).

Все функции требуют активного Flask app-контекста (запросы — всегда есть).
"""
from datetime import datetime, timezone

from db import db
from models import Customer, Event, Order


def utcnow():
    return datetime.now(timezone.utc)


def normalize_phone(phone):
    """Нормализация телефона в канонический вид +7XXXXXXXXXX (CL-2).

    «8 900 …», «+7 900 …» и «900 …» (10 цифр) приводятся к одному ключу —
    иначе один и тот же клиент дублировался бы в базе. Прочие номера —
    без изменений (цифры с сохранением ведущего +).
    """
    digits = ''.join(c for c in str(phone or '') if c.isdigit())
    if len(digits) == 11 and digits[0] in ('7', '8'):
        return '+7' + digits[1:]
    if len(digits) == 10:
        return '+7' + digits
    plus = '+' if str(phone or '').strip().startswith('+') else ''
    return plus + digits


def _coerce_product_id(pid):
    """Как раньше _norm_product_id: int, если строка из цифр, иначе str."""
    if pid is None:
        return ''
    if isinstance(pid, int):
        return str(pid)
    s = str(pid)
    return s


def _display_product_id(pid):
    s = str(pid or '')
    return int(s) if s.isdigit() else s


def _touch(customer):
    customer.updated_at = utcnow()
    customer.last_activity_at = utcnow()


def add_customer(name, phone, email='', consent_pd=False):
    """Добавить или обновить клиента по телефону (CL-1, CL-2)."""
    phone_norm = normalize_phone(phone)
    customer = db.session.query(Customer).filter_by(phone=phone_norm).first()
    if customer is None and email:
        # Дополнительная дедупликация по email (CL-2)
        customer = db.session.query(Customer).filter_by(email=email).first()
        if customer is not None and customer.phone and customer.phone != phone_norm:
            customer = None  # телефон — главный ключ; email совпал случайно
    now = utcnow()
    if customer is None:
        customer = Customer(phone=phone_norm, created_at=now)
        db.session.add(customer)
    customer.name = name or customer.name or ''
    customer.email = email or customer.email or ''
    if consent_pd:
        customer.consent_pd = True
        customer.consent_at = now
    _touch(customer)
    db.session.commit()
    return customer.to_dict()


def get_customer_by_phone(phone):
    customer = db.session.query(Customer).filter_by(phone=normalize_phone(phone)).first()
    return customer.to_dict() if customer else None


def add_order(name, phone, email, product_id, product_name, price, qty=1,
              source='oneclick', status='new', customer_id=None):
    """Новая заявка + гарантированное создание клиента (CL-1)."""
    if customer_id is None:
        customer = add_customer(name, phone, email)
        customer_id = customer['id']
    else:
        existing = db.session.get(Customer, customer_id)
        if existing is None:
            customer = add_customer(name, phone, email)
            customer_id = customer['id']
    order = Order(
        customer_id=customer_id,
        name=name or '',
        phone=normalize_phone(phone),
        email=email or '',
        product_id=_coerce_product_id(product_id),
        product_name=product_name or '',
        price=int(price or 0),
        qty=max(1, int(qty or 1)),
        status=status,
        source=source,
    )
    db.session.add(order)
    customer = db.session.get(Customer, customer_id)
    if customer is not None:
        _touch(customer)
    db.session.commit()
    d = order.to_dict()
    d['product_id'] = _display_product_id(d['product_id'])
    return d


def get_customers():
    return [c.to_dict() for c in db.session.query(Customer).order_by(Customer.id).all()]


def get_customer_orders(customer_id):
    orders = (db.session.query(Order)
              .filter_by(customer_id=customer_id)
              .order_by(Order.id.desc()).all())
    result = []
    for o in orders:
        d = o.to_dict()
        d['product_id'] = _display_product_id(d['product_id'])
        result.append(d)
    return result


def get_orders():
    result = []
    for o in db.session.query(Order).order_by(Order.id).all():
        d = o.to_dict()
        d['product_id'] = _display_product_id(d['product_id'])
        result.append(d)
    return result


def update_order_status(order_id, status):
    order = db.session.get(Order, int(order_id))
    if order is None:
        return None
    order.status = status
    order.updated_at = utcnow()
    db.session.commit()
    d = order.to_dict()
    d['product_id'] = _display_product_id(d['product_id'])
    return d


def add_event(etype, product_id=None, product_name='', customer_id=None,
              session_id='', query=''):
    """Записать событие (RS-1): view|like|order|favorite|search."""
    db.session.add(Event(
        type=etype,
        customer_id=customer_id,
        session_id=session_id or '',
        product_id=_coerce_product_id(product_id),
        product_name=product_name or '',
        query=query or '',
    ))
    db.session.commit()


def add_like(product_id, product_name='', customer_id=None, session_id=''):
    """Публичный лайк товара (как раньше, плюс событие)."""
    add_event('like', product_id=product_id, product_name=product_name,
              customer_id=customer_id, session_id=session_id)


def _int_key(pid):
    s = str(pid or '')
    return int(s) if s.isdigit() else s


def get_like_counts():
    """{product_id: count} — ключи int, где возможно (совместимость)."""
    rows = (db.session.query(Event.product_id, db.func.count(Event.id))
            .filter_by(type='like')
            .group_by(Event.product_id).all())
    return {_int_key(pid): count for pid, count in rows if pid}


def get_customer_likes(customer_id):
    """Лайкнутые товары клиента (LC-3)."""
    rows = (db.session.query(Event)
            .filter_by(type='like', customer_id=customer_id)
            .order_by(Event.id.desc()).all())
    seen, result = set(), []
    for e in rows:
        pid = _int_key(e.product_id)
        if pid in seen:
            continue
        seen.add(pid)
        result.append({'product_id': pid, 'product_name': e.product_name or ''})
    return result


def _product_key(p):
    """Универсальный id товара из dict (WB nm_id или Ozon offer_id)."""
    return p.get('nm_id') or p.get('offer_id') or p.get('id') or 0


def get_popular_products(products, limit=8, like_weight=1):
    """Топ товаров по заказам + лайкам из базы клиентов (RS-2, fallback RS-5)."""
    order_counts = {}
    for o in db.session.query(Order.product_id).all():
        pid = _int_key(o.product_id)
        if pid != '':
            order_counts[pid] = order_counts.get(pid, 0) + 1
    like_counts = get_like_counts()

    scored = []
    for p in products:
        pid = _product_key(p)
        if not pid:
            continue
        pid_key = _int_key(pid)
        orders_n = order_counts.get(pid_key, 0)
        likes_n = like_counts.get(pid_key, 0)
        score = orders_n + likes_n * like_weight
        if score > 0:
            scored.append({'product': p, 'score': score,
                           'orders': orders_n, 'likes': likes_n})

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:limit]


def get_customer_stats(customer_id):
    """Сумма покупок и последняя активность для профиля (CL-3)."""
    orders = db.session.query(Order).filter_by(customer_id=customer_id).all()
    total = sum(o.price * o.qty for o in orders)
    customer = db.session.get(Customer, customer_id)
    return {
        'orders_count': len(orders),
        'total_spent': total,
        'last_activity_at': customer.last_activity_at.isoformat() if customer and customer.last_activity_at else '',
    }

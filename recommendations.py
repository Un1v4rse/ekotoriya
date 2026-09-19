# -*- coding: utf-8 -*-
"""Движок рекомендаций (RS-2…RS-6).

Скоринг: score = Σ (вес_события × затухание(возраст)), где
    веса: заказ > лайк > просмотр (настраиваются в админке, RS-3),
    затухание — линейное: factor = max(0, 1 − возраст_дней/decay_days) (RS-4).

Уровни (RS-2):
    popular_products()  — общие топ-N (анонимы, холодный старт — RS-5)
    personal_products() — по истории клиента (customer_id или session_id);
                          при пустой истории — fallback на популярное
    similar_products()  — «похожие»: категория/бренд/цена ±30% + совместные
                          покупки (RS-6)
    also_bought()       — «с этим покупают»: совместная покупаемость

Пересчёт для cron: jobs/recompute_recommendations.py пишет в таблицу
recommendations (глобальные popular + персональные по клиентам).
"""
from datetime import datetime, timezone

from db import db
from models import Customer, Event, Order

DEFAULT_SETTINGS = {
    'w_order': 3.0,      # вес заказа (RS-3)
    'w_like': 2.0,       # вес лайка/избранного
    'w_view': 1.0,       # вес просмотра
    'decay_days': 30,    # период затухания, дней (RS-4)
    'limit': 8,          # сколько товаров в блоке
}


def utcnow():
    return datetime.now(timezone.utc)


def get_settings():
    """Настройки из site.recommendations (хранятся в site_settings, RS-7)."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        import storage
        saved = (storage.get_setting('site') or {}).get('recommendations') or {}
        for key in settings:
            if key in saved:
                try:
                    settings[key] = float(saved[key]) if key.startswith('w_') else int(saved[key])
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return settings


def _age_days(created_at):
    if not created_at:
        return 0.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0.0, (utcnow() - created_at).total_seconds() / 86400.0)


def _decay(created_at, decay_days):
    """Линейное затухание: свежие события важнее (RS-4)."""
    if not decay_days or decay_days <= 0:
        return 1.0
    return max(0.0, 1.0 - _age_days(created_at) / decay_days)


def _events_scope(customer_id=None, session_id=None):
    q = db.session.query(Event)
    if customer_id:
        return q.filter(Event.customer_id == customer_id)
    if session_id:
        return q.filter(Event.session_id == session_id)
    return q


def _orders_scope(customer_id=None):
    q = db.session.query(Order)
    if customer_id:
        q = q.filter(Order.customer_id == customer_id)
    return q


def score_products(products, customer_id=None, session_id=None, settings=None,
                   exclude_keys=None):
    """{product_key: score} по событиям/заказам выбранного scope."""
    if settings is None:
        settings = get_settings()
    decay_days = settings.get('decay_days', 30)
    scores = {}
    exclude_keys = exclude_keys or set()

    for e in _events_scope(customer_id, session_id).all():
        pid = str(e.product_id or '')
        if not pid:
            continue
        if e.type == 'like':
            w = settings.get('w_like', 2.0)
        elif e.type == 'view':
            w = settings.get('w_view', 1.0)
        else:
            continue
        scores[pid] = scores.get(pid, 0.0) + w * _decay(e.created_at, decay_days)

    for o in _orders_scope(customer_id).all():
        pid = str(o.product_id or '')
        if not pid:
            continue
        qty = o.qty or 1
        scores[pid] = scores.get(pid, 0.0) + settings.get('w_order', 3.0) * qty * _decay(o.created_at, decay_days)

    # нормализация ключей под формат товаров и исключения
    result = {}
    for p in products:
        key = str(p.get('nm_id') or p.get('id') or '')
        full_key = f"{p.get('source', 'wb')}:{key}"
        if full_key in exclude_keys or key in exclude_keys:
            continue
        score = scores.get(key, 0.0)
        if score > 0:
            result[full_key] = score
    return result


def _rank(products, scores, limit):
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    by_key = {}
    for p in products:
        by_key[f"{p.get('source', 'wb')}:{p.get('nm_id') or p.get('id')}"] = p
    out = []
    for key, score in ranked:
        if key in by_key:
            item = dict(by_key[key])
            item['rec_score'] = round(score, 3)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def popular_products(products, limit=None, settings=None):
    """Общие рекомендации: топ по всем событиям и заказам (RS-2, RS-5)."""
    if settings is None:
        settings = get_settings()
    if limit is None:
        limit = settings.get('limit', 8)
    scores = score_products(products, settings=settings)
    return _rank(products, scores, limit)


def personal_products(products, customer_id=None, session_id=None, limit=None,
                      settings=None):
    """Персональные рекомендации по истории клиента (RS-2).

    История пуста → fallback на популярное (RS-5, холодный старт).
    """
    if settings is None:
        settings = get_settings()
    if limit is None:
        limit = settings.get('limit', 8)
    liked_or_bought = set()
    scores = score_products(products, customer_id=customer_id,
                            session_id=session_id, settings=settings)
    if not scores:
        return popular_products(products, limit=limit, settings=settings)
    # не рекомендуем то, что клиент уже лайкал/покупал
    scope = _events_scope(customer_id, session_id)
    for e in scope.filter(Event.type == 'like').all():
        liked_or_bought.add(str(e.product_id))
    for o in _orders_scope(customer_id).all():
        liked_or_bought.add(str(o.product_id))
    filtered = {k: v for k, v in scores.items()
                if k.split(':', 1)[1] not in liked_or_bought}
    ranked = _rank(products, filtered, limit)
    if not ranked:  # всё уже лайкнул — покажем популярное
        return popular_products(products, limit=limit, settings=settings)
    return ranked


def _co_purchase_counts():
    """{product_id: {co_product_id: count}} по совместным заявкам."""
    orders = db.session.query(Order.customer_id, Order.product_id).all()
    by_customer = {}
    for customer_id, pid in orders:
        pid = str(pid or '')
        if pid:
            by_customer.setdefault(customer_id, set()).add(pid)
    co = {}
    for pids in by_customer.values():
        for a in pids:
            for b in pids:
                if a != b:
                    co.setdefault(a, {}).setdefault(b, 0)
                    co[a][b] += 1
    return co


def similar_products(products, product, limit=8):
    """«Похожие товары» (RS-6): категория/бренд/цена ±30% + совместные покупки."""
    key = str(product.get('nm_id') or product.get('id') or '')
    category = product.get('category') or ''
    brand = product.get('brand') or ''
    price = product.get('final_price') or product.get('price') or 0
    co = _co_purchase_counts().get(key, {})

    scored = []
    for p in products:
        pid = str(p.get('nm_id') or p.get('id') or '')
        if pid == key:
            continue
        score = 0.0
        if category and p.get('category') == category:
            score += 3.0
        if brand and p.get('brand') == brand:
            score += 2.0
        p_price = p.get('final_price') or p.get('price') or 0
        if price and p_price:
            ratio = abs(p_price - price) / price
            if ratio <= 0.3:
                score += 2.0 * (1 - ratio / 0.3)
        score += 4.0 * co.get(pid, 0)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(p, rec_score=round(s, 3)) for s, p in scored[:limit]]


def also_bought(products, product, limit=8):
    """«С этим покупают» — чистая совместная покупаемость."""
    key = str(product.get('nm_id') or product.get('id') or '')
    co = _co_purchase_counts().get(key, {})
    by_pid = {str(p.get('nm_id') or p.get('id')): p for p in products}
    ranked = sorted(co.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for pid, count in ranked:
        if pid in by_pid:
            out.append(dict(by_pid[pid], rec_score=count))
        if len(out) >= limit:
            break
    return out


def recompute_all(products=None, include_customers=True):
    """Полный пересчёт рекомендаций в таблицу recommendations (RS-5, cron)."""
    from models import Recommendation
    if products is None:
        from models import Product
        products = [p.to_product_dict() for p in
                    db.session.query(Product).filter_by(visible=True).all()]
    settings = get_settings()
    db.session.query(Recommendation).delete()
    rows = []

    for rank, p in enumerate(popular_products(products, limit=50, settings=settings)):
        rows.append(Recommendation(customer_id=None, kind='popular',
                                   product_id=str(p.get('nm_id') or p.get('id')),
                                   score=p.get('rec_score', 0)))
    if include_customers:
        for c in db.session.query(Customer).all():
            personal = personal_products(products, customer_id=c.id,
                                         limit=settings.get('limit', 8),
                                         settings=settings)
            for p in personal:
                rows.append(Recommendation(customer_id=c.id, kind='personal',
                                           product_id=str(p.get('nm_id') or p.get('id')),
                                           score=p.get('rec_score', 0)))
    db.session.add_all(rows)
    db.session.commit()
    return len(rows)

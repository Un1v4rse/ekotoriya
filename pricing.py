# -*- coding: utf-8 -*-
"""Система цен и скидок (PR-1…PR-7).

Формула итоговой цены (решение заказчика, PR-2):

    база = discount_price, если задана (> 0), иначе price
    процент:  итог = база × (1 − admin_percent/100)
    фикс. сумма (PR-4): итог = база − admin_amount (не ниже 1 руб.)

Округление — до целых рублей в пользу клиента (PR-6, math.floor).
Скидка маркетплейса (mp_discount_percent) в итог НЕ входит: она уже
учтена в discount_price. Суммирование wb+admin убрано (PR-5).

Действующая скидка на товар разрешается по приоритету:
    1. индивидуальная на товаре (Product.admin_discount_*)
    2. правило из таблицы discounts: category → source → all
"""
import math

from db import db
from models import Discount


def base_price(product):
    """База для расчёта: цена МП со скидкой, иначе обычная (PR-2, PR-1)."""
    discounted = int(product.get('discount_price') or 0)
    if discounted > 0:
        return discounted
    return int(product.get('price') or 0)


def compute_final_price(product, percent=None, discount_type=None, amount=None):
    """Итоговая цена сайта. Параметры скидки — если None, берутся из товара."""
    base = base_price(product)
    if not base:
        return 0
    dtype = discount_type if discount_type is not None else product.get('admin_discount_type', 'percent')
    if dtype == 'amount':
        a = amount if amount is not None else int(product.get('admin_discount_amount') or 0)
        result = base - max(0, int(a))
    else:
        p = percent if percent is not None else int(product.get('admin_discount_percent') or 0)
        p = min(max(int(p), 0), 99)
        result = base * (1 - p / 100)
    # округление в пользу клиента (PR-6)
    result = math.floor(result)
    return max(result, 1)


def _active_rules():
    """Активные правила скидок из таблицы discounts (PR-3)."""
    try:
        return db.session.query(Discount).filter_by(active=True).all()
    except Exception:
        db.session.rollback()
        return []


def resolve_admin_discount(product, rules=None):
    """Действующая скидка сайта на товар: (percent, type, amount).

    Приоритет: индивидуальная на товаре → category → source → all.
    Если скидки нет — (0, 'percent', 0).
    """
    individual = int(product.get('admin_discount_percent') or 0)
    ind_amount = int(product.get('admin_discount_amount') or 0)
    ind_type = product.get('admin_discount_type') or 'percent'
    if individual > 0 or (ind_type == 'amount' and ind_amount > 0):
        return (individual, ind_type, ind_amount)

    if rules is None:
        rules = _active_rules()
    category = str(product.get('category') or '')
    source = str(product.get('source') or '')
    for rule in rules:
        if rule.scope == 'category' and category and rule.scope_value == category:
            return (rule.value if rule.discount_type == 'percent' else 0,
                    rule.discount_type, rule.value if rule.discount_type == 'amount' else 0)
    for rule in rules:
        if rule.scope == 'source' and source and rule.scope_value == source:
            return (rule.value if rule.discount_type == 'percent' else 0,
                    rule.discount_type, rule.value if rule.discount_type == 'amount' else 0)
    for rule in rules:
        if rule.scope == 'all':
            return (rule.value if rule.discount_type == 'percent' else 0,
                    rule.discount_type, rule.value if rule.discount_type == 'amount' else 0)
    return (0, 'percent', 0)


def final_price(product, rules=None):
    """Итоговая цена сайта с учётом индивидуальной скидки и правил (PR-2)."""
    percent, dtype, amount = resolve_admin_discount(product, rules)
    return compute_final_price(product, percent, dtype, amount)


def enrich_product(product, rules=None):
    """Дописать в dict товара итоговую цену, скидку сайта и бейдж (PR-2)."""
    if rules is None:
        rules = _active_rules()
    percent, dtype, amount = resolve_admin_discount(product, rules)
    final = compute_final_price(product, percent, dtype, amount)
    product['final_price'] = final
    product['site_discount_percent'] = percent if dtype == 'percent' else 0
    mp_base = base_price(product)
    if mp_base and final < mp_base:
        product['site_discount_badge'] = round((1 - final / mp_base) * 100)
    else:
        product['site_discount_badge'] = 0
    return product


def enrich_products(products, rules=None):
    if rules is None:
        rules = _active_rules()
    return [enrich_product(p, rules) for p in products]

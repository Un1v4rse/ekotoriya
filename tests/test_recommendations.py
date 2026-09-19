# -*- coding: utf-8 -*-
"""Рекомендательная система (RS-2…RS-6): веса, затухание, персональные,
похожие, «с этим покупают» (задача 3.2, 3.6)."""
from datetime import timedelta

import customers
import recommendations
from db import db
from models import Event, Order


def _products():
    return [
        {'nm_id': 101, 'name': 'Наматрасник А', 'category': 'Наматрасники',
         'brand': 'Экотория', 'final_price': 1000, 'source': 'wb'},
        {'nm_id': 102, 'name': 'Наматрасник Б', 'category': 'Наматрасники',
         'brand': 'Экотория', 'final_price': 1100, 'source': 'wb'},
        {'nm_id': 103, 'name': 'Подушка', 'category': 'Подушки',
         'brand': 'Экотория', 'final_price': 900, 'source': 'wb'},
        {'nm_id': 104, 'name': 'Одеяло', 'category': 'Одеяла',
         'brand': 'Другой', 'final_price': 3000, 'source': 'ozon'},
    ]


def _settings(**over):
    s = dict(recommendations.DEFAULT_SETTINGS)
    s.update(over)
    return s


def test_weights_order_gt_like_gt_view():
    c = customers.add_customer('Веса', '+7 900 100-00-01')
    customers.add_event('view', product_id='101', customer_id=c['id'])
    customers.add_event('like', product_id='102', customer_id=c['id'])
    customers.add_order('Веса', '+7 900 100-00-01', '', '103', 'Подушка', 900)
    scores = recommendations.score_products(_products(), customer_id=c['id'],
                                          settings=_settings())
    assert scores['wb:103'] > scores['wb:102'] > scores['wb:101']


def test_decay_old_event_weaker():
    c = customers.add_customer('Затухание', '+7 900 100-00-02')
    customers.add_event('like', product_id='101', customer_id=c['id'])
    customers.add_event('like', product_id='102', customer_id=c['id'])
    old = (db.session.query(Event)
           .filter_by(customer_id=c['id'], product_id='101').first())
    old.created_at = old.created_at - timedelta(days=20)
    db.session.commit()
    scores = recommendations.score_products(_products(), customer_id=c['id'],
                                          settings=_settings(decay_days=30))
    assert scores['wb:102'] > scores['wb:101']


def test_decay_zero_after_period():
    c = customers.add_customer('Старое', '+7 900 100-00-03')
    customers.add_event('like', product_id='101', customer_id=c['id'])
    e = db.session.query(Event).filter_by(customer_id=c['id']).first()
    e.created_at = e.created_at - timedelta(days=60)
    db.session.commit()
    scores = recommendations.score_products(_products(), customer_id=c['id'],
                                          settings=_settings(decay_days=30))
    assert 'wb:101' not in scores


def test_personal_excludes_liked_and_fallback():
    products = _products()
    anon = recommendations.personal_products(products, session_id='empty-session')
    assert anon == recommendations.popular_products(products, limit=8)
    c = customers.add_customer('Лайкер', '+7 900 100-00-04')
    customers.add_event('view', product_id='101', customer_id=c['id'])
    customers.add_like('103', 'Подушка', customer_id=c['id'])
    personal = recommendations.personal_products(products, customer_id=c['id'])
    ids = {str(p.get('nm_id')) for p in personal}
    assert '103' not in ids      # лайкнутое не рекомендуем
    assert '101' in ids          # просмотренное — рекомендуем
    # всё лайкнул → fallback на популярное
    customers.add_like('101', 'Наматрасник А', customer_id=c['id'])
    fallback = recommendations.personal_products(products, customer_id=c['id'])
    assert fallback == recommendations.popular_products(products, limit=8)


def test_similar_by_category_price():
    products = _products()
    base = products[0]
    similar = recommendations.similar_products(products, base)
    assert similar, 'похожие не найдены'
    assert similar[0]['nm_id'] == 102  # та же категория, бренд и цена ±30%


def test_also_bought_by_co_purchase():
    products = _products()
    customers.add_order('Покупатель', '+7 900 100-00-05', '', '101', 'Наматрасник А', 1000)
    customers.add_order('Покупатель', '+7 900 100-00-05', '', '103', 'Подушка', 900)
    also = recommendations.also_bought(products, products[0])
    assert also and str(also[0]['nm_id']) == '103'


def test_recompute_all_writes_table():
    customers.add_like('101', 'Наматрасник А', session_id='s')
    n = recommendations.recompute_all(products=_products())
    assert n >= 1
    from models import Recommendation
    kinds = {r.kind for r in db.session.query(Recommendation).all()}
    assert 'popular' in kinds

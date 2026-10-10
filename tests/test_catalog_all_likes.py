# -*- coding: utf-8 -*-
"""Режим «все товары» каталога (per_page=all) и персистентность избранного
(серверная разметка лайкнутых товаров для восстановления состояния после F5)."""

import pytest

import app as app_module
import customers
from db import db
from models import Product


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed_products():
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def _add_products(n, start=900000000):
    """Добавить n тестовых товаров напрямую в БД."""
    for i in range(n):
        db.session.add(Product(source='wb', nm_id=start + i,
                               name=f'Товар тестовый {i}', price=1000 + i,
                               visible=True, stock=5))
    db.session.commit()
    app_module.invalidate_site_cache()


# ---------- режим «все товары» ----------

def test_catalog_per_page_all_renders_everything(client):
    _seed_products()
    _add_products(20)  # 8 demo + 20 = 28 товаров (> 24 на страницу)
    r = client.get('/catalog/?per_page=all')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert body.count('wb-product-card') == 28
    # пагинация не рендерится (class встречается и в инжектируемом JS —
    # проверяем именно разметку)
    assert 'class="wb-catalog-pagination"' not in body


def test_catalog_default_still_paginates(client):
    _seed_products()
    _add_products(20)
    r = client.get('/catalog/')
    body = r.data.decode('utf-8')
    assert body.count('wb-product-card') == 24
    assert 'class="wb-catalog-pagination"' in body


def test_catalog_per_page_all_keeps_filters(client):
    _seed_products()
    _add_products(20)
    r = client.get('/catalog/?per_page=all&source=wb&sort=price_asc')
    assert r.status_code == 200
    assert r.data.decode('utf-8').count('wb-product-card') == 28


# ---------- персистентность избранного ----------

def test_visitor_likes_by_session_and_customer(client):
    _seed_products()
    customers.add_like(890129778, 'А', session_id='sid-A')
    customers.add_like(67928567, 'Б', session_id='sid-A')
    customers.add_like(890129778, 'А', session_id='sid-A')  # дубликат
    anon = customers.get_visitor_likes(session_id='sid-A')
    assert [l['product_id'] for l in anon] == [67928567, 890129778]
    assert customers.get_visitor_likes(session_id='sid-other') == []
    # лайк аккаунта не виден анонимной сессии и наоборот
    cid = customers.register_client(phone='+7 900 300-00-08',
                                    password='secret1', name='К')[0]['id']
    customers.add_like(381073941, 'В', customer_id=cid)
    assert [l['product_id'] for l in customers.get_visitor_likes(customer_id=cid)] == [381073941]


def test_liked_state_injected_for_anonymous_session(client):
    _seed_products()
    with client.session_transaction() as s:
        s['sid'] = 'sid-inject-1'
    customers.add_like(890129778, 'Наматрасник', session_id='sid-inject-1')
    for path in ('/catalog/', '/catalog/namatrasniki/'):
        r = client.get(path)
        body = r.data.decode('utf-8')
        assert '"890129778"' in body
        assert 'window.__likesCount=1;' in body


def test_liked_state_injected_for_customer(client):
    _seed_products()
    reg = client.post('/api/client/register', json={
        'name': 'Любитель', 'phone': '+7 900 300-00-09', 'password': 'secret1'})
    cid = reg.get_json()['customer']['id']
    customers.add_like(890129778, 'Наматрасник', customer_id=cid)
    r = client.get('/catalog/')
    body = r.data.decode('utf-8')
    assert '"890129778"' in body
    assert 'window.__likesCount=1;' in body


def test_liked_state_empty_by_default(client):
    _seed_products()
    r = client.get('/catalog/')
    body = r.data.decode('utf-8')
    assert 'window.__likedIds=[];' in body
    assert 'window.__likesCount=0;' in body

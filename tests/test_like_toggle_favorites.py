# -*- coding: utf-8 -*-
"""Тoggle лайка (/api/like), избранное из снапшота, очистка товаров-заглушек."""

import json

import pytest

import app as app_module
import customers
import stub_cleanup
from db import db
from models import Event, Product


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture(autouse=True)
def _clear_rate_limit_bucket():
    """/api/like и др. POST имеют общий in-memory rate-limit bucket:
    после наших тестов сбрасываем его, чтобы не отнимать лимит у
    test_security.py (там проверяется 429 на 21-м запросе)."""
    yield
    app_module._rate_buckets.clear()


def _seed_products():
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def _like_events(product_id):
    return (db.session.query(Event)
            .filter_by(type='like', product_id=str(product_id))
            .order_by(Event.id.desc()).all())


# ---------- toggle лайка ----------

def test_api_like_toggles(client):
    """Повторный клик снимает лайк: событие одно, второй POST удаляет его."""
    r = client.post('/api/like', json={'product_id': 555111222, 'product_name': 'Тоггл'})
    data = r.get_json()
    assert data['success'] and data['liked'] is True and data['likes'] == 1
    # второй клик — анлайк
    r = client.post('/api/like', json={'product_id': 555111222, 'product_name': 'Тоггл'})
    data = r.get_json()
    assert data['success'] and data['liked'] is False and data['likes'] == 0
    assert _like_events(555111222) == []
    # третий клик — снова лайк
    r = client.post('/api/like', json={'product_id': 555111222, 'product_name': 'Тоггл'})
    assert r.get_json()['liked'] is True
    assert len(_like_events(555111222)) == 1


def test_remove_like_keeps_other_visitors_likes():
    customers.add_like(777555111, 'А', session_id='sess-a')
    customers.add_like(777555111, 'А', session_id='sess-b')
    assert customers.is_liked(777555111, session_id='sess-a') is True
    removed = customers.remove_like(777555111, session_id='sess-a')
    assert removed == 1
    assert customers.is_liked(777555111, session_id='sess-a') is False
    assert customers.is_liked(777555111, session_id='sess-b') is True
    assert customers.get_like_counts().get(777555111) == 1


def test_like_dedup_customer_or_session():
    """Дедупликация срабатывает и по аккаунту, и по сессии (что известно)."""
    c = customers.add_customer('Тогглер', '+7 900 444-55-99')
    assert customers.add_like(777555222, 'Б', customer_id=c['id'],
                              session_id='s-1') is True
    # та же сессия, другой контекст (customer_id не передан) — уже лайкнуто
    assert customers.add_like(777555222, 'Б', session_id='s-1') is False
    # тот же аккаунт, новая сессия — уже лайкнуто
    assert customers.add_like(777555222, 'Б', customer_id=c['id'],
                              session_id='s-2') is False
    assert customers.get_like_counts().get(777555222) == 1


# ---------- снапшот в лайке ----------

def test_like_snapshot_stored_and_returned():
    c = customers.add_customer('Снапшот', '+7 900 444-55-88')
    customers.add_like(777555333, 'Наматрасник Тестовый', customer_id=c['id'],
                       snapshot={'name': 'Наматрасник Тестовый', 'price': 4999,
                                 'photo': '/static/upload/x.jpg',
                                 'url': '/catalog/namatrasniki/tovarnyy-777555333/',
                                 'source': ''})
    likes = customers.get_customer_likes(c['id'])
    assert len(likes) == 1
    snap = likes[0]['snapshot']
    assert snap['price'] == 4999
    assert snap['photo'] == '/static/upload/x.jpg'
    assert '777555333' in snap['url']
    # снапшот лежит в events.payload как JSON
    ev = _like_events(777555333)[0]
    assert json.loads(ev.payload)['price'] == 4999


def test_api_like_stores_snapshot(client):
    client.post('/api/like', json={
        'product_id': 777555444, 'product_name': 'Одеяло Снап',
        'price': 3200.4, 'photo': '/static/upload/y.jpg',
        'url': '/catalog/odeyala/odeyalo-777555444/', 'source': 'wb'})
    ev = _like_events(777555444)[0]
    snap = json.loads(ev.payload)
    assert snap['price'] == 3200  # округлено до целых
    assert snap['photo'] == '/static/upload/y.jpg'
    assert snap['source'] == 'wb'


# ---------- /personal/favorite/ из лайков ----------

def _register(client, phone):
    r = client.post('/api/client/register', json={
        'name': 'Фаворит', 'phone': phone, 'password': 'secret1'})
    assert r.get_json()['success']


def test_favorite_page_shows_snapshot_item(client):
    """Товар вне каталога (статичная aspro-карточка) показывается из снапшота."""
    _seed_products()
    _register(client, '+7 900 300-00-21')
    cust = customers.get_customer_by_phone('+7 900 300-00-21')
    customers.add_like(677984451, 'Наматрасник 200х220 на резинке',
                       customer_id=cust['id'],
                       snapshot={'name': 'Наматрасник 200х220 на резинке',
                                 'price': 5999,
                                 'photo': '',
                                 'url': '/catalog/namatrasniki/t-677984451/',
                                 'source': ''})
    r = client.get('/personal/favorite/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'Наматрасник 200х220 на резинке' in body
    assert '5999 ₽' in body
    assert 'href="/catalog/namatrasniki/t-677984451/"' in body
    # без фото — НЕ noimage-заглушка, а нейтральный блок
    assert '/static/noimage.png' not in body


def test_favorite_page_shows_catalog_item(client):
    """Товар из каталога показывается с живыми данными и внутренней ссылкой."""
    _seed_products()
    _register(client, '+7 900 300-00-22')
    cust = customers.get_customer_by_phone('+7 900 300-00-22')
    customers.add_like(890129778, 'Непромокаемый наматрасник 200х200',
                       customer_id=cust['id'])
    r = client.get('/personal/favorite/')
    body = r.data.decode('utf-8')
    assert 'Непромокаемый наматрасник 200х200' in body
    assert 'href="/catalog/wb/890129778/"' in body


def test_favorite_page_skips_nameless_items(client):
    """Лайк без имени и снапшота не превращается в «заглушку» на странице."""
    _seed_products()
    _register(client, '+7 900 300-00-23')
    cust = customers.get_customer_by_phone('+7 900 300-00-23')
    customers.add_like(999888777, '', customer_id=cust['id'])  # вне каталога, без имени
    r = client.get('/personal/favorite/')
    body = r.data.decode('utf-8')
    # карточек нет — только пустое состояние (id товара живёт в инжекте шапки)
    assert 'class="lk-product"' not in body
    assert 'Нет избранных товаров' in body


# ---------- очистка товаров-заглушек ----------

def _add_product(nm_id, name, price=1000, photo='/static/upload/p.jpg', stock=10):
    p = Product(source='wb', nm_id=nm_id, name=name, price=price,
                photo=photo, stock=stock)
    db.session.add(p)
    db.session.commit()
    return p


def test_stub_cleanup_finds_only_stubs():
    stub1 = _add_product(900000001, 'Заглушка без фото', price=500,
                         photo='', stock=0)
    stub2 = _add_product(900000002, 'Заглушка нулевая цена', price=0,
                         photo='/static/upload/z.jpg', stock=0)
    live_no_stock = _add_product(900000003, 'Живой, нет в наличии', price=1500,
                                 photo='/static/upload/a.jpg', stock=0)
    live_in_stock = _add_product(900000004, 'Живой в наличии', price=2000,
                                 photo='', stock=5)
    stubs = stub_cleanup.find_stub_products()
    ids = {s['id'] for s in stubs}
    assert stub1.id in ids and stub2.id in ids
    assert live_no_stock.id not in ids   # с фото и ценой — не заглушка
    assert live_in_stock.id not in ids   # в наличии — не трогаем


def test_stub_cleanup_removes_stubs():
    stub = _add_product(900000010, 'На удаление', price=0, photo='', stock=0)
    keep = _add_product(900000011, 'Остаётся', price=900,
                        photo='/static/upload/k.jpg', stock=0)
    removed = stub_cleanup.remove_stub_products()
    assert [s['id'] for s in removed] == [stub.id]
    assert db.session.get(Product, stub.id) is None
    assert db.session.get(Product, keep.id) is not None

# -*- coding: utf-8 -*-
"""«Купить в 1 клик» (/api/cart/buy-one-click), привязка анонимных лайков
к клиенту при входе, фикс «Показать ещё» на категорийных страницах."""

import pytest

import app as app_module
import customers
from db import db


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed_products():
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


# ---------- «Купить в 1 клик» ----------

def test_buy_one_click_replaces_cart(client):
    _seed_products()
    client.post('/api/cart/add', json={'product_id': '67928567', 'source': 'wb', 'qty': 3})
    r = client.post('/api/cart/buy-one-click', json={'product_id': '890129778'})
    data = r.get_json()
    assert data['success'] and data['cart'] == {'wb:890129778': 1}
    assert data['redirect'] == '/basket/?oneclick=1'
    # в корзине только этот товар
    r = client.get('/basket/?oneclick=1')
    assert r.status_code == 200
    assert 'Непромокаемый наматрасник 200х200'.encode('utf-8') in r.data
    assert 'Наматрасник 160х200 на молнии высота 21-23'.encode('utf-8') not in r.data
    # подсказка про оформление в 1 клик
    assert 'Купить в 1 клик'.encode('utf-8') in r.data


def test_buy_one_click_unknown_product_with_meta(client):
    """Товар, которого нет в каталоге (статичная aspro-карточка): имя/цена
    от клиента сохраняются в cart_meta — корзина и заявка осмысленны."""
    _seed_products()
    r = client.post('/api/cart/buy-one-click', json={
        'product_id': '677984451', 'product_name': 'Наматрасник 200х220 на резинке',
        'price': 5999.85})
    assert r.get_json()['success']
    r = client.get('/basket/?oneclick=1')
    body = r.data.decode('utf-8')
    assert 'Наматрасник 200х220 на резинке' in body
    assert '5 999' in body or '5999' in body
    # оформление заявки с внешним товаром
    r = client.post('/api/cart/checkout', json={
        'name': 'Покупатель', 'phone': '+7 900 300-00-07'})
    data = r.get_json()
    assert data['success'] and len(data['orders']) == 1
    assert data['orders'][0]['product_name'] == 'Наматрасник 200х220 на резинке'


def test_buy_one_click_known_product_ignores_meta(client):
    _seed_products()
    client.post('/api/cart/buy-one-click', json={
        'product_id': '890129778', 'product_name': 'Подмена', 'price': 1})
    r = client.get('/basket/?oneclick=1')
    body = r.data.decode('utf-8')
    assert 'Непромокаемый наматрасник 200х200' in body
    assert 'Подмена' not in body


def test_buy_one_click_requires_product(client):
    r = client.post('/api/cart/buy-one-click', json={})
    assert r.status_code == 400


# ---------- «В корзину» мёртвых aspro-карточек (перехват .to-cart) ----------

def test_cart_add_unknown_product_with_meta(client):
    """Товара нет в каталоге (карточки aspro-клона): имя/цена от клиента
    сохраняются в cart_meta — корзина показывает осмысленную позицию."""
    _seed_products()
    r = client.post('/api/cart/add', json={
        'product_id': '1254', 'source': 'aspro',
        'product_name': 'Одеяло евро всесезонное', 'price': 2390})
    data = r.get_json()
    assert data['success'] and data['count'] == 1
    r = client.get('/basket/')
    body = r.data.decode('utf-8')
    assert 'Одеяло евро всесезонное' in body
    assert '2 390' in body or '2390' in body


def test_cart_add_known_product_ignores_meta(client):
    """Для товаров каталога клиентские имя/цена игнорируются (как в 1-клике)."""
    _seed_products()
    r = client.post('/api/cart/add', json={
        'product_id': '890129778', 'product_name': 'Подмена', 'price': 1})
    assert r.get_json()['success']
    r = client.get('/basket/')
    body = r.data.decode('utf-8')
    assert 'Непромокаемый наматрасник 200х200' in body
    assert 'Подмена' not in body


def test_cart_add_accumulates_qty(client):
    _seed_products()
    for _ in range(3):
        r = client.post('/api/cart/add', json={'product_id': '67928567'})
        assert r.get_json()['success']
    assert r.get_json()['cart'] == {'wb:67928567': 3}


def test_cart_add_after_register_session(client):
    """Свежая регистрация не ломает добавление: сессия сохраняется,
    корзина доступна сразу (сценарий «зависания после регистрации»)."""
    _seed_products()
    reg = client.post('/api/client/register', json={
        'name': 'Новый клиент', 'phone': '+7 900 300-00-05', 'password': 'secret1'})
    assert reg.get_json()['success']
    r = client.post('/api/cart/add', json={'product_id': '890129778'})
    assert r.get_json()['success']
    r = client.get('/basket/')
    assert r.status_code == 200
    assert 'Непромокаемый наматрасник 200х200'.encode('utf-8') in r.data


def test_buy_one_click_anonymous_empty_checkout_fields(client):
    """Без входа поля оформления просто пустые."""
    _seed_products()
    client.post('/api/cart/buy-one-click', json={'product_id': '890129778'})
    r = client.get('/basket/?oneclick=1')
    assert r.status_code == 200
    assert 'id="co-name" name="name" value=""'.encode('utf-8') in r.data
    assert 'id="co-phone" name="phone" value=""'.encode('utf-8') in r.data


# ---------- лайки: аноним → вход ----------

def test_anonymous_like_bound_to_customer_on_login(client):
    _seed_products()
    # анонимный лайк — событие сессии, как записывает /api/like
    # (через API не делаем: у тестов общий in-memory rate-limit bucket)
    with client.session_transaction() as s:
        s['sid'] = 'anon-sid-1'
    customers.add_like(890129778, 'Непромокаемый наматрасник',
                       session_id='anon-sid-1')
    # избранное анониму недоступно — редирект на /auth/
    r = client.get('/personal/favorite/')
    assert r.status_code == 302
    # регистрация → лайки сессии переходят клиенту
    reg = client.post('/api/client/register', json={
        'name': 'Любитель', 'phone': '+7 900 300-00-06', 'password': 'secret1'})
    assert reg.get_json()['success']
    r = client.get('/personal/favorite/')
    assert r.status_code == 200
    assert 'Непромокаемый наматрасник 200х200'.encode('utf-8') in r.data


def test_bind_session_likes_idempotent():
    _seed_products()
    from flask import session
    with app_module.app.test_request_context():
        session['sid'] = 'testsid123'
        assert customers.bind_session_likes(None, 'testsid123') == 0


# ---------- «Показать ещё» на категорийных страницах ----------

def test_category_pagination_fix_injected(client):
    """Кнопка «Показать ещё» больше не подгружает страницу целиком:
    инлайн-фикс ведёт на каталог в режиме «все товары», мёртвые .one_click
    не прячутся."""
    _seed_products()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    assert 'var B="/catalog/?per_page=all";' in html
    assert '.one_click{display:none' not in html


def test_category_pagination_fix_uses_category_filter(client):
    """Если у товаров раздела заполнена category — ссылка фильтрует по ней."""
    _seed_products()
    from models import Product
    with app_module.app.app_context():
        for p in db.session.query(Product).all():
            p.category = 'Наматрасники'
        db.session.commit()
    app_module.invalidate_site_cache()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    assert 'var B="/catalog/?category=' in html
    assert 'per_page=all' in html


# ---------- «В корзину»: инлайн-перехват (CART_INTERCEPT_JS) ----------

def test_cart_intercept_inline_on_all_entry_pages(client):
    """Перехват кнопок «В корзину» живёт INLINE в HTML (CART_INTERCEPT_JS),
    а не во внешнем wb-actions.js: у части клиентов внешние JS-файлы
    приходят обрезанными (прокси/DPI портят gzip-статику) — обрезанный файл
    это SyntaxError и молча мёртвые кнопки. HTML страницы не gzip'уются.
    Флаг гарантирует, что wb-actions.js не дублирует обработчики."""
    _seed_products()
    # /personal/favorite/ и /personal/ для анонима редиректят на вход.
    pages = ['/', '/catalog/', '/catalog/namatrasniki/',
             '/catalog/wb/890129778/', '/basket/']
    for url in pages:
        r = client.get(url)
        assert r.status_code == 200, url
        body = r.data.decode('utf-8')
        assert 'window.__wbCartIntercept=true' in body, url
        # оба типа кнопок + устойчивость (таймаут, r.ok, блокировка повтора)
        assert "closest('.wb-cart-btn')" in body, url
        assert "closest('.to-cart')" in body, url
        assert 'AbortController' in body, url
        assert "'/api/cart/add'" in body, url


def test_wb_actions_js_has_no_cart_handlers():
    """wb-actions.js больше не обрабатывает корзину (иначе при живом файле
    запрос уходил бы дважды — инлайн-перехват + файл)."""
    import os
    path = os.path.join(os.path.dirname(app_module.__file__),
                        'static', 'js', 'wb-actions.js')
    with open(path, encoding='utf-8') as f:
        js = f.read()
    assert 'addStaticToCart' not in js
    assert '/api/cart/add' not in js

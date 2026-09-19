# -*- coding: utf-8 -*-
"""Ozon Seller API (OZ-1…OZ-6, TS-1): синк в БД, цены в сотых, скидки, журнал."""
import pytest

import ozon_sync
import wb_sync

CLIENT_ID = '123456'
API_KEY = 'ozon-secret-key'

INFO_ITEMS = {
    111: {'id': 111, 'name': 'Наматрасник 160х200 Экотория', 'offer_id': 'NM-160',
          'images': ['https://cdn.ozon.ru/img-111.jpg', 'https://cdn.ozon.ru/img-111-2.jpg'],
          'category': 'Наматрасники', 'rating': 4.8, 'reviews_amount': 42},
    222: {'id': 222, 'name': 'Наматрасник 180х200 Экотория', 'offer_id': 'NM-180',
          'images': ['https://cdn.ozon.ru/img-222.jpg'],
          'rating': 4.5, 'reviews_amount': 7},  # category может отсутствовать
}

# Цены приходят в сотых рублях
PRICE_ITEMS = {
    111: {'product_id': 111, 'price': {'old_price': '399000', 'price': '359100',
                                       'marketing_price': '359100', 'min_price': '10000',
                                       'currency_code': 'RUB'}},
    222: {'product_id': 222, 'price': {'old_price': '0', 'price': '289000',
                                       'marketing_price': '0', 'min_price': '10000',
                                       'currency_code': 'RUB'}},
}

STOCK_ITEMS = [
    {'product_id': 111, 'present': 15},
    {'product_id': 111, 'present': 5},   # суммирование по складам
    {'product_id': 222, 'present': 0},
]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def make_fake_post(routes, calls):
    """Фейковый requests.post: маршрутизация по URL, заодно записывает вызовы."""
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({'url': url, 'headers': headers or {}, 'json': json or {}})
        for prefix, payload in routes.items():
            if url.endswith(prefix):
                return FakeResponse(payload)
        raise AssertionError(f'Неожиданный URL: {url}')
    return fake_post


def ozon_routes():
    return {
        '/v3/product/list': {'result': {'items': [{'product_id': 111}, {'product_id': 222}],
                                        'last_id': ''}},
        '/v3/product/info/list': {'result': {'items': list(INFO_ITEMS.values())}},
        '/v5/product/info/prices': {'result': {'items': list(PRICE_ITEMS.values())}},
        '/v4/product/info/stocks': {'result': {'items': STOCK_ITEMS}},
    }


@pytest.fixture
def ozon_creds(monkeypatch):
    monkeypatch.setenv('OZON_CLIENT_ID', CLIENT_ID)
    monkeypatch.setenv('OZON_API_KEY', API_KEY)
    return CLIENT_ID, API_KEY


def test_get_ozon_credentials_env_priority(monkeypatch, ozon_creds):
    """Env-переменные имеют приоритет над настройками сайта (OZ-1)."""
    import storage
    site = storage.get_setting('site') or {}
    site['ozon_client_id'] = 'from-storage'
    site['ozon_api_key'] = 'storage-key'
    storage.set_setting('site', site)
    assert ozon_sync.get_ozon_credentials() == (CLIENT_ID, API_KEY)


def test_get_ozon_credentials_from_storage(monkeypatch):
    """Без env креденшелы берутся из site_settings (OZ-1)."""
    monkeypatch.delenv('OZON_CLIENT_ID', raising=False)
    monkeypatch.delenv('OZON_API_KEY', raising=False)
    import storage
    site = storage.get_setting('site') or {}
    site['ozon_client_id'] = 'storage-id'
    site['ozon_api_key'] = 'storage-key'
    storage.set_setting('site', site)
    assert ozon_sync.get_ozon_credentials() == ('storage-id', 'storage-key')


def test_build_product_mapping():
    """OZ-3: единый dict товара из карточки Ozon и ценового блока."""
    info = INFO_ITEMS[111]
    item = ozon_sync.build_product(info, {'price': 3990, 'discount_price': 3591,
                                          'mp_discount_percent': 10})
    assert item['id'] == 111 and item['nm_id'] == 111
    assert item['source'] == 'ozon'
    assert item['name'] == info['name']
    assert item['brand'] == ''
    assert item['vendor_code'] == 'NM-160'
    assert item['category'] == 'Наматрасники'
    assert item['price'] == 3990
    assert item['discount_price'] == 3591
    assert item['wb_discount_percent'] == 10
    assert item['admin_discount_percent'] == 0
    assert item['admin_discount_type'] == 'percent'
    assert item['admin_discount_amount'] == 0
    assert item['photo'] == 'https://cdn.ozon.ru/img-111.jpg'
    assert item['stock'] == 0
    assert item['url'] == 'https://www.ozon.ru/product/160-200-111/'


def test_build_product_no_discount_and_no_photo():
    """Скидки нет → discount_price=0; фото нет → пустая строка; category=''."""
    info = dict(INFO_ITEMS[222])  # категории нет — поле может отсутствовать
    info['images'] = []
    item = ozon_sync.build_product(info, {'price': 2890, 'discount_price': 0,
                                          'mp_discount_percent': 0})
    assert item['category'] == ''
    assert item['discount_price'] == 0
    assert item['photo'] == ''
    assert item['url'].endswith('-222/')


def test_price_conversion_from_cents():
    """Цены в сотых делятся на 100; marketing < old → скидка МП (OZ-5)."""
    base, discounted, percent = ozon_sync._price_block_to_prices(
        PRICE_ITEMS[111]['price'])
    assert (base, discounted, percent) == (3990, 3591, 10)
    # без marketing-скидки: base = price, discount_price = 0
    base, discounted, percent = ozon_sync._price_block_to_prices(
        PRICE_ITEMS[222]['price'])
    assert (base, discounted, percent) == (2890, 0, 0)
    # нулевая база → None (товар пропускается)
    assert ozon_sync._price_block_to_prices({'old_price': '0', 'price': '0'}) is None


def test_sync_persists_products_to_db(monkeypatch, ozon_creds):
    """sync() пишет товары в БД с source='ozon', цены в рублях, заголовки Ozon (OZ-2)."""
    calls = []
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(ozon_routes(), calls))

    result = ozon_sync.sync()
    assert result['success'] and result['count'] == 2

    products = ozon_sync.load_products()
    assert len(products) == 2
    for p in products:
        assert p['source'] == 'ozon'
        assert p['url'].startswith('https://www.ozon.ru/product/')

    first = next(p for p in products if p['nm_id'] == 111)
    assert first['price'] == 3990            # 399000 сотых → 3990 руб.
    assert first['discount_price'] == 3591   # 359100 сотых → 3591 руб.
    assert first['wb_discount_percent'] == 10
    assert first['stock'] == 20              # 15 + 5, суммирование по складам
    second = next(p for p in products if p['nm_id'] == 222)
    assert second['discount_price'] == 0
    assert second['stock'] == 0

    # заголовки аутентификации Ozon (OZ-2)
    list_call = next(c for c in calls if c['url'].endswith('/v3/product/list'))
    assert list_call['headers']['Client-Id'] == CLIENT_ID
    assert list_call['headers']['Api-Key'] == API_KEY
    assert list_call['headers']['Content-Type'] == 'application/json'
    # цены запрошены по product_id из /v3/product/list
    prices_call = next(c for c in calls if c['url'].endswith('/v5/product/info/prices'))
    assert sorted(prices_call['json']['filter']['product_id']) == [111, 222]


def test_admin_discount_survives_resync(monkeypatch, ozon_creds):
    """Скидка админа сохраняется после повторной полной синхронизации (OZ-3/OZ-8)."""
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(ozon_routes(), []))
    ozon_sync.sync()

    from db import db
    from models import Product
    prod = db.session.query(Product).filter_by(source='ozon', nm_id=111).one()
    prod.admin_discount_percent = 15
    prod.admin_discount_type = 'percent'
    db.session.commit()

    ozon_sync.sync()  # повторный синк из «маркетплейса»

    updated = next(p for p in ozon_sync.load_products() if p['nm_id'] == 111)
    assert updated['admin_discount_percent'] == 15
    assert updated['admin_discount_type'] == 'percent'
    assert wb_sync.final_price(updated) < updated['price']


def test_sync_log_written_with_ozon_source(monkeypatch, ozon_creds):
    """Журнал синхронизации пишется с source='ozon' (через wb_sync)."""
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(ozon_routes(), []))
    ozon_sync.sync()
    log = wb_sync.get_sync_log(limit=10, source='ozon')
    assert len(log) >= 1
    assert log[0]['source'] == 'ozon'
    assert log[0]['status'] == 'ok'
    assert log[0]['count'] == 2


def test_sync_light_updates_prices_and_stocks_only(monkeypatch, ozon_creds):
    """sync_light() обновляет цены/остатки без запроса карточек (OZ-5/OZ-6)."""
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(ozon_routes(), []))
    ozon_sync.sync()

    new_routes = {
        '/v5/product/info/prices': {'result': {'items': [
            {'product_id': 111, 'price': {'old_price': '499000', 'price': '449100',
                                          'marketing_price': '449100', 'min_price': '10000',
                                          'currency_code': 'RUB'}},
            {'product_id': 222, 'price': {'old_price': '0', 'price': '279000',
                                          'marketing_price': '0', 'min_price': '10000',
                                          'currency_code': 'RUB'}},
        ]}},
        '/v4/product/info/stocks': {'result': {'items': [
            {'product_id': 111, 'present': 3},
            {'product_id': 222, 'present': 8},
        ]}},
    }
    calls = []
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(new_routes, calls))

    result = ozon_sync.sync_light()
    assert result['success'] and result['light'] and result['count'] == 2

    # карточки не запрашивались — только цены и остатки
    assert not any(c['url'].endswith('/v3/product/list') for c in calls)
    assert not any(c['url'].endswith('/v3/product/info/list') for c in calls)

    products = {p['nm_id']: p for p in ozon_sync.load_products()}
    assert products[111]['price'] == 4990
    assert products[111]['discount_price'] == 4491
    assert products[111]['wb_discount_percent'] == 10
    assert products[111]['stock'] == 3
    assert products[222]['price'] == 2790
    assert products[222]['stock'] == 8
    # имя не затирается (карточки не запрашивались)
    assert products[111]['name'] == INFO_ITEMS[111]['name']


def test_sync_light_keeps_prices_when_zero_base(monkeypatch, ozon_creds):
    """Товар с нулевой базовой ценой в ответе API сохраняет прежние цены (OZ-5)."""
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(ozon_routes(), []))
    ozon_sync.sync()

    zero_routes = {
        '/v5/product/info/prices': {'result': {'items': [
            {'product_id': 222, 'price': {'old_price': '0', 'price': '0',
                                          'marketing_price': '0', 'min_price': '0',
                                          'currency_code': 'RUB'}},
        ]}},
        '/v4/product/info/stocks': {'result': {'items': []}},
    }
    monkeypatch.setattr(ozon_sync.requests, 'post', make_fake_post(zero_routes, []))

    result = ozon_sync.sync_light()
    assert result['success']
    products = {p['nm_id']: p for p in ozon_sync.load_products()}
    assert products[222]['price'] == 2890  # прежняя цена сохранена
    assert products[111]['price'] == 3990  # отсутствующий в ответе — тоже прежняя


def test_sync_without_credentials_fails(monkeypatch):
    """Без креденшелов — success=False и запись error в журнал с source='ozon'."""
    monkeypatch.delenv('OZON_CLIENT_ID', raising=False)
    monkeypatch.delenv('OZON_API_KEY', raising=False)
    result = ozon_sync.sync()
    assert result['success'] is False
    assert 'credentials' in result['error']
    log = wb_sync.get_sync_log(limit=5, source='ozon')
    assert log and log[0]['status'] == 'error'

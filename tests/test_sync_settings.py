# -*- coding: utf-8 -*-
"""Этап 8 (цикл 4): настройки синка WB/Ozon.

8.4 — статус синхронизации в «Интеграциях» (/admin/api/sync-status);
8.3 — режим «Скрыть маркетплейсы» (site.hide_marketplaces): бейджи WB/Ozon,
ссылки «Посмотреть на …» и «Нашли дешевле?» исчезают, покупка на сайте остаётся;
8.1/8.2 — расписание и выключение авто-синка (jobs/autosync.py): при выключенном
enabled джоб ничего не синхронизирует.
"""
import time

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import storage
import wb_sync
from db import db
from models import Product, SyncLog


@pytest.fixture()
def client():
    return app_module.app.test_client()


@pytest.fixture()
def admin_client():
    app_module.ADMIN_CREDENTIALS.update({
        'user': 'testadmin',
        'hash': generate_password_hash('testpass'),
        'must_change_password': False,
    })
    c = app_module.app.test_client()
    c.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    with c.session_transaction() as s:
        s['csrf_token'] = 'csrf'
    return c


def _seed_ozon():
    """Демо-каталог WB + один товар Ozon."""
    wb_sync.sync(token=None, demo=True)
    db.session.add(Product(source='ozon', nm_id=777000111,
                           name='Одеяло Ozon тест', price=2000, discount_price=0,
                           photo='', stock=5,
                           url='https://www.ozon.ru/product/odeyalo-777000111/'))
    db.session.commit()
    app_module.invalidate_site_cache()
    return wb_sync.load_products()[0]['nm_id']


def _set_site(**fields):
    content = storage.load_content()
    content.setdefault('site', {}).update(fields)
    storage.save_content(content)
    app_module.invalidate_site_cache()


# ---------- 8.4: статус синхронизации ----------

def test_sync_status_endpoint(admin_client):
    r = admin_client.get('/admin/api/sync-status')
    data = r.get_json()
    assert data['success']
    for src in ('wb', 'ozon'):
        st = data['status'][src]
        assert 'enabled' in st
        assert st['catalog_hours'] and st['light_minutes']


def test_sync_status_requires_admin(client):
    r = client.get('/admin/api/sync-status')
    assert r.status_code in (302, 401, 403)


def test_sync_status_shows_last_log(admin_client):
    db.session.add(SyncLog(source='wb', status='ok', message='тестовый синк',
                           count=10))
    db.session.commit()
    st = admin_client.get('/admin/api/sync-status').get_json()['status']['wb']
    assert st['last_log'] is not None
    assert st['last_log']['message'] == 'тестовый синк'
    assert st['last_ok'] is not None


# ---------- 8.3: режим «Скрыть маркетплейсы» ----------

def test_hide_mp_hides_catalog_badges(client):
    _seed_ozon()
    html = client.get('/catalog/').get_data(as_text=True)
    assert '>Ozon<' in html and '>WB<' in html
    _set_site(hide_marketplaces=True)
    html = client.get('/catalog/').get_data(as_text=True)
    assert '>Ozon<' not in html
    assert '>WB<' not in html


def test_hide_mp_applies_to_api_catalog(client):
    _seed_ozon()
    html = client.get('/api/catalog').get_json()['html']
    assert '>Ozon<' in html and '>WB<' in html
    _set_site(hide_marketplaces=True)
    html = client.get('/api/catalog').get_json()['html']
    assert '>Ozon<' not in html
    assert '>WB<' not in html


def test_hide_mp_product_page(client):
    wb_nm = _seed_ozon()
    html = client.get(f'/catalog/wb/{wb_nm}/').get_data(as_text=True)
    assert 'Посмотреть на Wildberries' in html
    assert 'Нашли дешевле?' in html
    html = client.get('/catalog/ozon/777000111/').get_data(as_text=True)
    assert 'Посмотреть на Ozon' in html
    _set_site(hide_marketplaces=True)
    html = client.get(f'/catalog/wb/{wb_nm}/').get_data(as_text=True)
    assert 'Посмотреть на' not in html
    assert 'Нашли дешевле?' not in html
    # покупка на самом сайте остаётся
    assert 'В корзину' in html
    assert 'Купить в 1 клик' in html
    html = client.get('/catalog/ozon/777000111/').get_data(as_text=True)
    assert 'Посмотреть на Ozon' not in html


# ---------- 8.1/8.2: расписание и выключение (jobs/autosync.py) ----------

def test_autosync_disabled_skips_sync():
    from jobs import autosync
    storage.set_setting('site', {'wb_autosync': {'enabled': False,
                                                 'catalog_hours': 1,
                                                 'light_minutes': 5}})
    assert autosync.run_source('wb') is False


def test_autosync_enabled_but_recent_state_skips():
    from jobs import autosync
    storage.set_setting('site', {'wb_autosync': {'enabled': True,
                                                 'catalog_hours': 1,
                                                 'light_minutes': 5}})
    now = time.time()
    autosync.save_state({'wb': {'last_catalog': now, 'last_light': now}})
    assert autosync.run_source('wb') is False

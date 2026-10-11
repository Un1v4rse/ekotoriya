# -*- coding: utf-8 -*-
"""Страница товара: характеристики из админки + редактируемые подписи блоков.

Покрытие:
- характеристики, заданные в админке (extra товара), показываются на странице
  товара (панель «Характеристики» и вкладка «Характеристики»);
- служебные ключи extra (description, seo_*, gallery, stock_label, variants)
  в характеристиках не отображаются;
- подписи «Нашли дешевле?», «Хочу в подарок», текст доставки и блока
  консультации берутся из настроек сайта (site.*), пусто -> дефолтные тексты;
- подписи эскейпятся (HTML из настройки не выполняется).
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import storage
import wb_sync
from db import db
from models import Product


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
    client = app_module.app.test_client()
    resp = client.post('/admin/login', data={'username': 'testadmin', 'password': 'testpass'})
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sess['csrf_token'] = 'test-csrf'
    return client


def _csrf():
    return {'X-CSRF-Token': 'test-csrf', 'Content-Type': 'application/json'}


def _seed():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    return wb_sync.load_products()[0]['nm_id']


def _set_site(**fields):
    content = storage.load_content()
    content.setdefault('site', {}).update(fields)
    storage.save_content(content)
    app_module.invalidate_site_cache()


def _product_page(client, nm):
    resp = client.get(f'/catalog/wb/{nm}/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_admin_characteristics_shown_on_product_page(admin_client, client):
    nm = _seed()
    resp = admin_client.post('/admin/api/product-update',
                             json={'nm_id': nm,
                                   'characteristics': {'Материал': 'Микрофибра',
                                                       'Плотность': '120 г/м²'}},
                             headers=_csrf())
    assert resp.get_json()['success'] is True
    html = _product_page(client, nm)
    assert 'Материал' in html and 'Микрофибра' in html
    assert 'Плотность' in html and '120 г/м²' in html


def test_service_extra_keys_not_shown_as_characteristics(client):
    nm = _seed()
    product = db.session.query(Product).filter_by(source='wb', nm_id=nm).first()
    product.extra = {'description': 'Текст описания',
                     'seo_title': 'SEO заголовок',
                     'seo_description': 'SEO описание',
                     'gallery': ['https://example.com/1.jpg'],
                     'stock_label': 'В наличии',
                     'variants': {'colors': ['белый']},
                     'Материал': 'Хлопок'}
    db.session.commit()
    app_module.invalidate_site_cache()
    html = _product_page(client, nm)
    assert 'Материал' in html and 'Хлопок' in html
    # служебные значения не попадают в список характеристик
    for service in ('https://example.com/1.jpg',):
        assert service not in html.split('Характеристики', 1)[1].split('wb-detail-tabs')[0]


def test_product_labels_defaults(client):
    nm = _seed()
    html = _product_page(client, nm)
    assert 'Нашли дешевле?' in html
    assert 'Хочу в подарок' in html
    assert 'Доставка по России' in html
    assert 'Нужна консультация?' in html
    assert 'ЗАДАТЬ ВОПРОС' in html


def test_product_labels_from_site_settings(client):
    nm = _seed()
    _set_site(product_cheaper_label='Где дешевле?',
              product_delivery_text='Доставка курьером за 1 день',
              product_gift_label='Оформить как подарок',
              product_consult_title='Остались вопросы?',
              product_consult_text='Напишите нам — ответим быстро',
              product_consult_button='Написать нам')
    html = _product_page(client, nm)
    assert 'Где дешевле?' in html
    assert 'Доставка курьером за 1 день' in html
    assert 'Оформить как подарок' in html
    assert 'Остались вопросы?' in html
    assert 'Напишите нам — ответим быстро' in html
    assert 'НАПИСАТЬ НАМ' in html
    # дефолтные тексты заменены
    assert 'Нашли дешевле?' not in html
    assert 'Нужна консультация?' not in html


def test_product_labels_escaped(client):
    nm = _seed()
    _set_site(product_gift_label='<script>alert(1)</script>')
    html = _product_page(client, nm)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_product_labels_empty_setting_falls_back(client):
    nm = _seed()
    _set_site(product_cheaper_label='   ')
    html = _product_page(client, nm)
    assert 'Нашли дешевле?' in html

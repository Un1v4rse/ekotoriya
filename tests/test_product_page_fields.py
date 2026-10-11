# -*- coding: utf-8 -*-
"""Простой список товаров в админке + новые поля карточки товара.

Покрытие:
- список товаров в админке без контролов скидки/раздела/редактирования,
  карточка кликабельна (курсор/обработчик в шаблоне);
- API: gallery (список URL), stock_label (текст наличия), variants
  (группы чипов colors/heights/sizes) — редактирование и создание;
- страница товара показывает: свою галерею, свой текст наличия, явные
  варианты чипами; per-product seo_title попадает в <title>;
- синхронизация WB не затирает новые поля (extra синком не пишется),
  закрепление фото/остатка продолжает работать.
"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import wb_sync
from db import db
from models import Product


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


def _get(nm):
    return db.session.query(Product).filter_by(source='wb', nm_id=nm).first()


def test_admin_product_list_clean_and_clickable(admin_client):
    _seed()
    html = admin_client.get('/admin/').data.decode('utf-8')
    # контролы скидки/раздела/редактирования из карточки убраны
    assert 'disc-row' not in html
    assert 'sect-select' not in html
    assert 'edit-row' not in html
    assert 'setProductDiscount' not in html
    assert 'setProductSection' not in html
    # карточка кликабельна: hover-подсветка + обработчик клика
    assert '.prod-card:hover' in html
    assert "cursor: pointer" in html
    assert "card.addEventListener('click'" in html
    # массовое назначение скидки/раздела (bulk) осталось
    assert 'applySelectedDiscount' in html
    assert 'applySelectedSection' in html
    # модалка содержит новые поля
    for field in ('pe-gallery', 'pe-stock-label', 'pe-variants'):
        assert f'id="{field}"' in html


def test_update_gallery_stock_label_variants(admin_client):
    nm = _seed()
    r = admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'product_id': nm,
        'gallery': ['/static/uploads/a.jpg', 'https://example.com/b.jpg'],
        'stock_label': 'Ожидается завтра',
        'variants': {'sizes': ['90x200', '120x200'], 'bogus': ['x']},
    }, headers=_csrf())
    assert r.get_json()['success']

    prod = _get(nm)
    assert prod.extra['gallery'] == ['/static/uploads/a.jpg', 'https://example.com/b.jpg']
    assert prod.extra['stock_label'] == 'Ожидается завтра'
    # неизвестные группы отбрасываем — чипы строятся только для 3 известных
    assert prod.extra['variants'] == {'sizes': ['90x200', '120x200']}

    d = prod.to_product_dict()
    assert d['gallery'] == prod.extra['gallery']
    assert d['stock_label'] == 'Ожидается завтра'
    assert d['variants'] == {'sizes': ['90x200', '120x200']}
    # служебные ключи не смешиваются с характеристиками
    assert 'gallery' not in d.get('characteristics', {})


def test_update_gallery_validation(admin_client):
    nm = _seed()
    r = admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'product_id': nm,
        'gallery': ['javascript:alert(1)'],
    }, headers=_csrf())
    assert r.status_code == 400
    r = admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'product_id': nm, 'gallery': 'not-a-list',
    }, headers=_csrf())
    assert r.status_code == 400


def test_product_page_shows_new_fields(admin_client):
    nm = _seed()
    admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'product_id': nm,
        'gallery': ['/static/uploads/a.jpg', '/static/uploads/b.jpg'],
        'stock_label': 'Последние 2 штуки',
        'variants': {'sizes': ['90x200', '120x200']},
        'seo_title': 'Уникальный SEO заголовок товара',
    }, headers=_csrf())

    html = admin_client.get(f'/catalog/wb/{nm}/').data.decode('utf-8')
    # галерея: главное изображение + миниатюры
    assert 'wb-main-img' in html
    assert '/static/uploads/a.jpg' in html and '/static/uploads/b.jpg' in html
    # свой текст наличия вместо авто
    assert 'Последние 2 штуки' in html
    # явные варианты — чипами на карточке
    assert '90x200' in html and '120x200' in html
    assert 'Размер' in html
    # per-product SEO title
    assert '<title>Уникальный SEO заголовок товара' in html


def test_product_page_stock_label_auto_and_empty_gallery(admin_client):
    nm = _seed()
    html = admin_client.get(f'/catalog/wb/{nm}/').data.decode('utf-8')
    assert 'Много' in html          # авто-бейдж по остатку из демо-синка
    assert 'wb-main-img' in html    # фото демо-товара


def test_product_create_with_new_fields(admin_client):
    _seed()
    r = admin_client.post('/admin/api/product-create', json={
        'name': 'Свой товар с галереей', 'price': 1000,
        'gallery': ['/static/uploads/g1.jpg'],
        'stock_label': 'Под заказ',
        'variants': {'colors': ['белый', 'серый']},
    }, headers=_csrf())
    body = r.get_json()
    assert body['success']
    prod = db.session.query(Product).filter_by(
        source='site', nm_id=body['product_id']).first()
    assert prod.extra['gallery'] == ['/static/uploads/g1.jpg']
    assert prod.extra['stock_label'] == 'Под заказ'
    assert prod.extra['variants'] == {'colors': ['белый', 'серый']}

    html = admin_client.get(body['url']).data.decode('utf-8')
    assert 'Под заказ' in html
    assert '/static/uploads/g1.jpg' in html


def test_wb_sync_keeps_extra_fields(admin_client):
    nm = _seed()
    admin_client.post('/admin/api/product-update', json={
        'source': 'wb', 'product_id': nm,
        'gallery': ['/static/uploads/a.jpg'],
        'stock_label': 'Свой текст',
        'variants': {'sizes': ['90x200', '120x200']},
        'photo': '/static/uploads/new-main.jpg',   # закрепляемое поле
    }, headers=_csrf())

    # имитация синка WB: новые данные маркетплейса
    wb_sync._persist_products([{
        'id': nm, 'nm_id': nm, 'name': 'Имя из WB', 'brand': 'БрендWB',
        'vendor_code': 'WB-1', 'price': 5000, 'discount_price': 4500,
        'wb_discount_percent': 10, 'photo': 'https://baskets.example/x.jpg',
        'url': f'https://www.wildberries.ru/catalog/{nm}/detail.aspx',
        'stock': 77, 'visible': True,
    }], demo=False)

    prod = _get(nm)
    # extra-содержимое синхронизация не затирает
    assert prod.extra['gallery'] == ['/static/uploads/a.jpg']
    assert prod.extra['stock_label'] == 'Свой текст'
    assert prod.extra['variants'] == {'sizes': ['90x200', '120x200']}
    # фото было закреплено ручной правкой — синк его не перезаписал
    assert prod.photo == '/static/uploads/new-main.jpg'
    assert 'photo' in (prod.manual_overrides or [])
    # остальные поля обновились
    assert prod.price == 5000 and prod.stock == 77

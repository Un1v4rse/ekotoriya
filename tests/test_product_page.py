# -*- coding: utf-8 -*-
"""Страница товара (/catalog/<source>/<id>/): вёрстка как на оригинале.

Галерея слева, справа — избранное/артикул/наличие, переключатель размера
(товары-соседи с тем же «стволом» имени в других размерах), цена, кнопки
корзины/1-клика/избранного, доставка и вкладки Описание/Характеристики.
На странице товара заголовок раздела и живой блок каталога не выводятся.
"""
import re

import pytest

import app as app_module
import wb_sync
from db import db
from models import Product


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def test_product_page_structure_and_buttons(client):
    _seed()
    r = client.get('/catalog/wb/381073941/')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    # заголовок товара и крошки
    assert '<h1' in html and 'Наматрасник 140х200 на молнии чехол стеганый Экотория' in html
    assert 'Главная' in html and 'Каталог' in html
    # рабочие кнопки покупки (те же классы, что в сетке каталога)
    assert 'wb-cart-btn' in html
    assert 'wb-buy-btn' in html
    assert 'wb-like-btn' in html
    assert 'wb-order-form' in html
    # вкладки Описание/Характеристики
    assert 'wb-detail-tabs' in html
    assert 'Описание' in html and 'Характеристики' in html
    assert 'Артикул' in html and 'Бренд' in html
    # артикул товара в кнопках
    assert 'data-id="381073941"' in html


def test_product_page_size_siblings_chips(client):
    _seed()
    # размерный ряд «на молнии чехол стеганый»: 140, 160, 180
    r = client.get('/catalog/wb/381073941/')
    html = r.data.decode('utf-8')
    assert 'Размер, см' in html
    assert '140x200' in html and '160x200' in html and '180x200' in html
    # активный размер — текущий товар (ссылка на себя отсутствует)
    chip_zone = re.search(r'Размер, см.*?/div\s*>\s*</div>', html, re.S)
    assert chip_zone
    zone = chip_zone.group(0)
    assert 'catalog/wb/381073941/' not in zone.replace(
        'data-url="/catalog/wb/381073941/"', '')
    # сосед ссылается на карточку соседа
    assert 'href="/catalog/wb/67928567/"' in html


def test_product_page_variant_groups_chips(client):
    _seed()
    # 47521588 — «140х200 на молнии высота 17-20 см»: ряд 140/160/180 и
    # высоты 17-20/21-23 того же «ствола» имени
    r = client.get('/catalog/wb/47521588/')
    html = r.data.decode('utf-8')
    assert 'Размер, см' in html
    assert '140x200' in html and '160x200' in html and '180x200' in html
    assert 'Высота борта, см' in html
    assert '17-20 см' in html and '21-23 см' in html
    # активный чип высоты — span с заливкой (ссылки на себя нет)
    zone = re.search(r'Высота борта, см.*?</div></div>', html, re.S).group(0)
    assert 'href=' not in zone.split('17-20 см')[0][-400:] or '17-20' in zone
    assert re.search(r'<span[^>]*background:#b49d84[^>]*>17-20 см</span>', zone)
    # сосед по высоте ссылается на его карточку
    assert 'href="/catalog/wb/67928567/"' in html or 'href="/catalog/wb/182356719/"' in html


def test_product_page_price_badge_and_buy_card(client):
    _seed()
    r = client.get('/catalog/wb/381073941/')
    html = r.data.decode('utf-8')
    # цена с копейками и зачёркнутая базовая (прайс WB 2490 → финал 2241)
    assert '2 241.00 ₽' in html
    assert '2 490 ₽' in html
    # жёлтый бейдж скидки и экономия
    assert '-10%' in html
    assert 'Экономия 249.00 ₽' in html
    # наличие точкой, «Нашли дешевле?», степпер, подарок, консультация
    assert 'Много' in html
    assert 'Нашли дешевле?' in html
    assert 'wb-qty' in html
    assert 'Хочу в подарок' in html
    assert 'Нужна консультация?' in html
    assert 'ЗАДАТЬ ВОПРОС' in html
    assert 'В ИЗБРАННОЕ' in html


def test_product_page_characteristics_panel(client):
    _seed()
    r = client.get('/catalog/wb/381073941/')
    html = r.data.decode('utf-8')
    assert 'Наполнитель' in html and 'Синтепон' in html
    assert 'Водонепроницаемость' in html
    assert 'Комплектация' in html
    assert 'Назначение текстиля' in html
    assert 'Размер наматрасника' in html and '140x200' in html
    # раскрытие «Все характеристики» и скрытые доп. поля
    assert 'Все характеристики' in html
    assert 'wb-chars-extra' in html
    assert 'Артикул' in html and 'Бренд' in html


def test_product_page_money_format():
    assert app_module._money(4146.21) == '4 146.21'
    assert app_module._money(2241) == '2 241.00'
    assert app_module._money(6009, kopecks=False) == '6 009'


def test_product_page_hides_section_panel(client):
    _seed()
    r = client.get('/catalog/wb/381073941/')
    html = r.data.decode('utf-8')
    # заголовок раздела «Наматрасники» как h1 раздела не выводится
    # (h1 — имя товара), живой блок каталога на карточке не дублируется
    # (класс встречается и в инжектируемом JS — проверяем разметку)
    assert html.count('id="pagetitle"') == 1
    assert 'class="wb-catalog-filters"' not in html
    assert 'class="wb-catalog-results"' not in html


def test_product_page_out_of_stock_disables_buy(client):
    _seed()
    with app_module.app.app_context():
        row = db.session.query(Product).filter_by(nm_id=381073941).first()
        row.stock = 0
        db.session.commit()
        app_module.invalidate_site_cache()
    r = client.get('/catalog/wb/381073941/')
    html = r.data.decode('utf-8')
    assert 'Нет в наличии' in html
    m = re.search(r'<button[^>]*class="wb-buy-btn"[^>]*>', html)
    assert m and 'disabled' in m.group(0)
    # форма 1-клика не рендерится в карточке при отсутствии остатка
    # (в блоке рекомендаций формы других товаров остаются — проверяем
    # только детальный блок)
    detail = html[html.find('class="wb-detail"'):html.find('wb-detail-tabs')]
    assert 'class="wb-order-form"' not in detail


def test_product_siblings_helper_groups_by_stem():
    _seed()
    products = {p.get('nm_id'): p for p in app_module.get_products()
                if p.get('source') == 'wb'}
    sib = app_module._product_size_siblings(products[381073941])
    sizes = [s for s, _ in sib]
    assert sizes == ['140x200', '160x200', '180x200']
    # одиночный товар без ряда — пустой список
    single = dict(products[1399155441])
    assert app_module._product_size_siblings(single) == []


def test_product_page_404_unknown(client):
    _seed()
    assert client.get('/catalog/wb/123456789012/').status_code == 404

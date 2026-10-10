# -*- coding: utf-8 -*-
"""Мёртвые битрикс-ссылки статичных aspro-карточек (замечание «клик по
карточке обновляет страницу»): рерайт на рабочие страницы товаров и
быстрая карточка-модалка для несопоставленных.

Статичные карточки категорийных страниц (снимок дизайна оригинального
магазина) ссылались на битрикс-URL товаров, которых в этом каталоге нет:
клик уходил в page_route и 302-редиректил на родительский раздел — страница
просто обновлялась. Починка: серверный рерайт HTML (ссылка с -<nm_id> →
/catalog/<source>/<nm_id>/; без id — сопоставление по имени карточки;
несопоставленная → href="#" + data-legacy-product → быстрая карточка-модалка),
плюс 301-редирект прямых заходов на -<nm_id>.
"""
import copy

import pytest

import app as app_module
import storage
import wb_sync
from db import db
from models import Product


@pytest.fixture()
def client():
    return app_module.app.test_client()

_MARKUP = """
<div class="catalog_block">
  <div class="catalog_item_wrapp">
    <div class="item-title"><a href="/catalog/namatrasniki/foobar-381073941/">Имя не из каталога</a></div>
  </div>
  <div class="catalog_item_wrapp">
    <div class="item-title"><a href="/catalog/namatrasniki/namatrasnik-140kh200-na-molnii-seryy/">Наматрасник 140х200 на молнии Серый</a></div>
  </div>
  <div class="catalog_item_wrapp">
    <div class="item-title"><a href="/catalog/namatrasniki/chto-to-zzz/">Абракадабра синяя маленькая</a></div>
  </div>
  <div class="catalog_item_wrapp">
    <div class="item-title"><a href="/catalog/namatrasniki/podrazdel/">Ссылка на подраздел</a></div>
  </div>
  <div class="catalog_item_wrapp">
    <div class="item-title"><a href="/catalog/wb/890129778/">Прямая wb-ссылка</a></div>
  </div>
</div>
"""


def _seed():
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()
    content = copy.deepcopy(storage.load_content())
    pages = content.setdefault('pages', [])
    pages.append({
        'slug': 'catalog__namatrasniki__podrazdel',
        'url': '/catalog/namatrasniki/podrazdel/',
        'title': 'Подраздел',
        'template': 'pages/catalog__namatrasniki.html',
        'blocks': [],
    })
    for p in pages:
        if p.get('slug') == 'catalog__namatrasniki':
            p['content_html'] = _MARKUP
    storage.save_content(content)
    app_module.invalidate_site_cache()


def test_legacy_link_with_nm_id_rewritten_to_product_page(client):
    """Ссылка с артикулом на конце (-<nm_id>) ведёт на рабочую страницу."""
    _seed()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'href="/catalog/wb/381073941/"' in body


def test_legacy_link_matched_by_card_name(client):
    """Ссылка без артикула сопоставляется по имени карточки с товаром."""
    _seed()
    r = client.get('/catalog/namatrasniki/')
    body = r.data.decode('utf-8')
    # «Наматрасник 140х200 на молнии» есть в демо-каталоге — ссылка рерайтится
    assert 'namatrasnik-140kh200-na-molnii-seryy' not in body
    assert body.count('href="/catalog/wb/381073941/"') >= 1


def test_legacy_link_unmatched_gets_modal_marker(client):
    """Несопоставленная ссылка: href="#" + data-legacy-product (модалка)."""
    _seed()
    r = client.get('/catalog/namatrasniki/')
    body = r.data.decode('utf-8')
    assert 'data-legacy-product="1"' in body
    assert 'data-legacy-href="/catalog/namatrasniki/chto-to-zzz/"' in body
    # исходный мёртвый href больше не href (остался только в data-атрибуте)
    assert '<a href="/catalog/namatrasniki/chto-to-zzz/"' not in body


def test_existing_subsection_link_not_rewritten(client):
    """Ссылки на существующие страницы-подразделы не трогаются."""
    _seed()
    r = client.get('/catalog/namatrasniki/')
    body = r.data.decode('utf-8')
    assert 'href="/catalog/namatrasniki/podrazdel/"' in body
    assert 'data-legacy-product="1" data-legacy-href="/catalog/namatrasniki/podrazdel/"' not in body


def test_direct_wb_link_not_rewritten(client):
    """Прямые рабочие ссылки /catalog/wb/<id>/ не меняются."""
    _seed()
    r = client.get('/catalog/namatrasniki/')
    body = r.data.decode('utf-8')
    assert 'href="/catalog/wb/890129778/"' in body
    assert 'data-legacy-href="/catalog/wb/890129778/"' not in body


def test_legacy_modal_assets_injected(client):
    """CSS/JS быстрой карточки инжектятся на страницы сайта."""
    _seed()
    for url in ('/', '/catalog/', '/catalog/namatrasniki/'):
        r = client.get(url)
        assert r.status_code == 200
        body = r.data.decode('utf-8')
        assert 'legacy-modal-overlay' in body
        assert 'window.__legacyModal' in body
        assert "closest('a[data-legacy-product]')" in body


def test_direct_visit_nm_id_url_redirects_301(client):
    """Прямой заход на битрикс-URL с артикулом: 301 на рабочую страницу."""
    _seed()
    r = client.get('/catalog/namatrasniki/lyuboy-slug-381073941/')
    assert r.status_code == 301
    assert r.headers.get('Location', '').endswith('/catalog/wb/381073941/')


def test_direct_visit_unknown_nm_id_keeps_parent_redirect(client):
    """Артикула нет в каталоге — прежнее поведение (302 на родительский раздел)."""
    _seed()
    r = client.get('/catalog/namatrasniki/slug-111222333/')
    assert r.status_code == 302
    assert r.headers.get('Location', '').endswith('/catalog/namatrasniki/')


def test_cart_accepts_legacy_product(client):
    """Корзина принимает «легаси»-товар с именем/ценой от клиента (cart_meta)."""
    r = client.post('/api/cart/add', json={
        'product_id': 'legacy-testovyy', 'source': 'wb',
        'product_name': 'Наматрасник тестовый', 'price': 6000})
    assert r.status_code == 200
    d = r.get_json()
    assert d['success'] is True
    assert d['count'] == 1

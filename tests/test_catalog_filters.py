# -*- coding: utf-8 -*-
"""Фильтры каталога (CT-1/CT-5): рабочая панель единого каталога сохраняет
применённые значения и пагинация не теряет параметры; мёртвый bitrix-
smartfilter категорийных страниц перехвачен и ведёт в единый каталог;
левое меню категорий свёрнуто по умолчанию (не перекрывает контент)."""

import copy

import pytest

import app as app_module
import storage
from db import db
from models import Product

# Минимальная разметка bitrix-клона для категорийной страницы: левое меню
# (.left_block) и мёртвый фильтр (form.smartfilter). В тестовом контенте
# content_html пуст, поэтому сидим её явно (в продовых данных/*.json она есть).
_SECTION_MARKUP = (
    '<div class="left_block sticky-sidebar"><div class="sticky-sidebar__inner">'
    '<div class="menu_top_block catalog_block"><div class="slide-block">'
    '<div class="slide-block__head">Каталог</div>'
    '<div class="slide-block__body"><ul class="menu dropdown">'
    '<li>Наматрасники</li><li>Одеяла</li><li>Наперники</li></ul></div>'
    '</div></div></div></div>'
    '<form action="/catalog/namatrasniki/" class="smartfilter" method="get">'
    '<input id="MAX_SMART_FILTER_P1_MIN" name="MAX_SMART_FILTER_P1_MIN" type="text" value=""/>'
    '<button id="set_filter1" type="button">Показать</button></form>'
)


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _seed_products():
    import wb_sync
    wb_sync.sync(token=None, demo=True)
    app_module.invalidate_site_cache()


def _add_products(n, start=900000000):
    for i in range(n):
        db.session.add(Product(source='wb', nm_id=start + i,
                               name=f'Товар тестовый {i}', price=1000 + i,
                               visible=True, stock=5))
    db.session.commit()
    app_module.invalidate_site_cache()


def _seed_section_markup():
    """Проставить content_html категорийной страницы (left_block + smartfilter)."""
    content = copy.deepcopy(storage.load_content())
    for p in content.get('pages', []):
        if p.get('slug') == 'catalog__namatrasniki':
            p['content_html'] = _SECTION_MARKUP
    storage.save_content(content)
    app_module.invalidate_site_cache()


def test_catalog_filter_values_restored(client):
    """Панель фильтров показывает применённые значения после перезагрузки."""
    _seed_products()
    r = client.get('/catalog/?price_from=1000&price_to=5000&sort=price_asc&in_stock=1')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'name="price_from" value="1000"' in body
    assert 'name="price_to" value="5000"' in body
    assert 'value="price_asc" selected' in body
    assert 'name="in_stock" value="1" checked' in body


def test_catalog_filter_applies_to_results(client):
    _seed_products()
    r = client.get('/catalog/?price_from=3000&price_to=4000')
    body = r.data.decode('utf-8')
    assert 'Найдено товаров:' in body
    # только товары с final_price в диапазоне 3000..4000 (3052 и 3321)
    assert body.count('wb-product-card') == 2


def test_catalog_pagination_keeps_filters(client):
    """Ссылки пагинации сохраняют параметры фильтра."""
    _seed_products()
    _add_products(20)  # 8 demo + 20 = 28 (> 24 на страницу)
    r = client.get('/catalog/?source=wb&sort=price_desc')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'wb-catalog-pagination' in body
    assert 'source=wb' in body
    assert 'sort=price_desc' in body
    # ссылки вида /catalog/?q=&amp;source=wb...&amp;page=2 (Jinja эскейпит &)
    assert 'page=2' in body
    assert '/catalog/?q=&amp;source=wb' in body


def test_section_page_smartfilter_intercepted_namtrasniki(client):
    """Мёртвый bitrix-фильтр категорий: в HTML есть форма smartfilter, но
    клики/сабмит перехватываются инжект-скриптом и ведут в единый каталог
    с параметрами price_from/price_to/sort."""
    _seed_products()
    _seed_section_markup()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    # разметка фильтра — из content_html, а не только из инжект-скрипта
    assert 'name="MAX_SMART_FILTER_P1_MIN"' in body
    assert 'id="set_filter1"' in body
    # инжект перехвата: оба обработчика (клик по «Показать» и submit формы)
    assert 'sfUrl' in body
    assert 'bx_filter_search_button' in body
    assert "classList.contains('smartfilter')" in body


# ---------- левое меню категорий: свёрнуто по умолчанию ----------


def test_section_leftmenu_collapsed_by_default(client):
    """Выпадашка левого меню (.left_block .slide-block__body) свёрнута
    по умолчанию и раскрывается по .opened/ховеру; приклеивание
    sticky-sidebar отключено — меню не перекрывает контент."""
    _seed_products()
    _seed_section_markup()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    # разметка левого меню — из content_html
    assert 'left_block sticky-sidebar' in body
    assert 'slide-block__head' in body
    # CSS-инжект: сворачивание + отключение affixed
    assert '.left_block .menu_top_block .slide-block__body{display:none' in body
    assert '.left_block.is-affixed .sticky-sidebar__inner{position:static' in body
    # JS-инжект: клик по шапке переключает .opened
    assert "classList.toggle('opened')" in body


# ---------- вёрстка /catalog/: ряд плиток подкатегорий не full-bleed ----------


def test_catalog_tiles_row_centered_layout_css(client):
    """Ряд плиток подкатегорий (.catalog_section_list) на /catalog/ тянулся
    во всю ширину экрана и на широких мониторах «прилипал» к краям, тогда
    как фильтры/SEO-тексты/сетка лежат в .maxwidth-theme (max-width:1408px,
    по центру). CSS-инжект ограничивает тот же ряд тем же контейнером;
    боковой padding действует только ≥1409px — мобильная вёрстка не
    затрагивается. Скоуп .main-catalog_wrapper/.catalog_section_list есть
    только на /catalog/ (у категорийных страниц его нет)."""
    r = client.get('/catalog/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert '.catalog_page .main-catalog-wrapper .catalog_section_list{' in body
    assert 'max-width:1408px !important' in body
    assert 'margin-left:auto !important' in body
    assert 'margin-right:auto !important' in body
    assert '@media (min-width:1409px)' in body
    assert 'padding-left:16px !important' in body
    assert 'padding-right:16px !important' in body


# ---------- вёрстка категорийных страниц: фильтр/сортировка не full-bleed ----------


def test_section_filter_panel_centered_layout_css(client):
    """Строка сортировки и панель smartfilter (.filter-panel-wrapper) на
    категорийных страницах тянулись во всю ширину экрана и «прилипали» к
    краям. CSS-инжект ограничивает их тем же контейнером 1408px, что и
    контент /catalog/; padding — только ≥1409px (мобильная вёрстка не
    трогается). Скоуп .with_left_block есть только у категорийных страниц:
    на /catalog/ этот класса у wrapper1 нет (и самой панели там нет)."""
    _seed_products()
    r = client.get('/catalog/namatrasniki/')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert '.wrapper1.with_left_block .filter-panel-wrapper{' in body
    assert 'max-width:1408px !important' in body
    assert 'margin-left:auto !important' in body
    assert 'margin-right:auto !important' in body
    assert '@media (min-width:1409px)' in body
    assert 'padding-left:16px !important' in body
    assert 'padding-right:16px !important' in body


# ---------- соцкнопки мобильной панели: стиль футерных кнопок ----------


def test_mobilemenu_socials_match_footer_style_css(client):
    """Иконки соцсетей в выдвижной мобильной панели (#mobilemenu, «сайдбар»
    на мобильных) шли общими aspro-правилами: квадратные (3px), без обводки,
    белые иконки, hover opacity:.8 — визуально не совпадали с круглыми
    обведёнными кнопками футера. CSS-инжект приводит панель к футерному
    стилю (круг 50%, обводка, покой opacity:.5 → hover 1 и брендовый ряд
    спрайта). Панель белая, поэтому используется «тёмный» ряд спрайта и
    обводка rgba(51,51,51,.2) (вариант footer .light) — прямой перенос
    светлого ряда с тёмного футера делал бы кнопки невидимыми. Скоуп
    #mobilemenu: футер и шапка не затрагиваются. Инжект — на всех страницах.
    """
    _seed_products()
    for url in ('/', '/catalog/', '/catalog/namatrasniki/'):
        r = client.get(url)
        assert r.status_code == 200
        body = r.data.decode('utf-8')
        assert '#mobilemenu .social-icons ul li a{' in body
        assert 'border-radius:50% !important' in body
        assert 'opacity:.5 !important' in body
        assert 'border:1px solid rgba(51,51,51,0.2) !important' in body
        assert '#mobilemenu .social-icons ul li a:hover{' in body
        assert 'opacity:1 !important' in body
        # сети, настроенные в админке (vk, telegram) + позиции спрайта
        assert '#mobilemenu .social-icons ul li.vk a{' in body
        assert 'background-position:0px -565px !important' in body
        assert '#mobilemenu .social-icons ul li.vk a:hover{background-position:0px -188px !important;}' in body
        assert '#mobilemenu .social-icons ul li.telegram a{' in body
        assert 'background-position:-330px -706px !important' in body
        assert '#mobilemenu .social-icons ul li.telegram a:hover{background-position:-329px -329px !important;}' in body

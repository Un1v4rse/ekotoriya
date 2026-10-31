# -*- coding: utf-8 -*-
"""Рендер блоков цикла 2 (CN-T4 обёртка/типографика/кнопки, CN-T8 zero-блок,
анимации): новые поля, защита от инъекций, fallback, обратная совместимость."""
import re

import pytest

import blocks as blocks_module
from blocks import render_block, render_blocks


def _norm(html):
    """Нормализация для сравнения разметки: хвостовые ';' в style не значимы."""
    return re.sub(r';"', '"', html)


# ---------- CN-T4: общая обёртка (фон/отступы/расположение) ----------

def test_wrapper_bg_gradient():
    html = render_block({'type': 'heading', 'text': 'Привет',
                         'bg_type': 'gradient', 'bg_gradient_from': '#ff0000',
                         'bg_gradient_to': '#0000ff', 'bg_gradient_angle': 45})
    assert 'cb-heading' in html
    assert 'background:linear-gradient(45deg,#ff0000,#0000ff)' in html


def test_wrapper_bg_image_with_position_size_repeat():
    html = render_block({'type': 'heading', 'text': 'Фон',
                         'bg_type': 'image', 'bg_image': '/static/img/bg.jpg',
                         'bg_image_position': 'center top',
                         'bg_image_size': 'contain', 'bg_image_repeat': 'repeat'})
    assert 'background:url(&quot;/static/img/bg.jpg&quot;)' in html or \
           'background:url("/static/img/bg.jpg")' in html
    assert 'center top/contain repeat' in html


def test_wrapper_bg_image_rejects_javascript_url():
    html = render_block({'type': 'heading', 'text': 'X',
                         'bg_type': 'image', 'bg_image': 'javascript:alert(1)'})
    assert 'javascript:' not in html
    assert 'background:url' not in html


def test_wrapper_bg_video_youtube_with_overlay():
    html = render_block({'type': 'heading', 'text': 'Видеофон',
                         'bg_type': 'video',
                         'bg_video': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                         'bg_opacity': 50})
    assert 'cb-bg-video' in html
    assert 'https://www.youtube.com/embed/dQw4w9WgXcQ' in html
    assert 'rgba(0,0,0,0.5)' in html
    assert 'position:relative' in html


def test_wrapper_bg_video_rutube():
    html = render_block({'type': 'heading', 'text': 'R',
                         'bg_type': 'video',
                         'bg_video': 'https://rutube.ru/video/abc123def/'})
    assert 'https://rutube.ru/play/embed/abc123def' in html


def test_wrapper_bg_video_rejects_bad_scheme():
    html = render_block({'type': 'heading', 'text': 'X',
                         'bg_type': 'video', 'bg_video': 'javascript:alert(1)'})
    assert 'cb-bg-video' not in html
    assert '<iframe' not in html


def test_wrapper_padding_and_min_height():
    html = render_block({'type': 'text', 'text': 'Текст',
                         'padding_top': 80, 'padding_bottom': 0,
                         'min_height': 300})
    assert 'padding-top:80px' in html
    assert 'padding-bottom:0px' in html
    assert 'min-height:300px' in html


def test_wrapper_legacy_padding_and_bg_and_text_color():
    html = render_block({'type': 'text_image', 'text': 'Т', 'src': '/i.png',
                         'bg': '#f5f5f5', 'padding': '40', 'text_color': '#333333'})
    style = re.search(r'style="([^"]*)"', html).group(1)
    assert 'background:#f5f5f5' in style
    assert 'padding-top:40px' in style
    assert 'padding-bottom:40px' in style
    assert 'color:#333333' in style
    # двойного применения нет: старый _style_attr не добавляется повторно
    assert style.count('background:') == 1
    assert style.count('padding-top:') == 1


def test_wrapper_container_full_adds_assets():
    html = render_blocks([{'type': 'text', 'text': 'X', 'container': 'full'}])
    assert 'cb-container-full' in html
    assert 'ANIMATIONS_ASSETS' not in html  # константа, не строка
    assert '.cb-container-full .maxwidth-theme' in html


def test_wrapper_content_align():
    html = render_block({'type': 'features', 'content_align': 'center',
                         'items': [{'title': 'А', 'text': 'Б'}]})
    assert 'text-align:center' in html


def test_wrapper_invalid_values_ignored():
    html = render_block({'type': 'heading', 'text': 'X',
                         'bg': '#zzz', 'padding': '9999',
                         'padding_top': -5, 'min_height': 5000,
                         'bg_type': 'gradient', 'bg_gradient_from': 'red'})
    style = re.search(r'style="([^"]*)"', html).group(1)
    assert 'background' not in style
    assert 'padding' not in style
    assert 'min-height' not in style
    assert 'cb-heading' in html


def test_wrapper_old_block_renders_same_markup():
    """Блок без новых полей: ключевые фрагменты прежней разметки на месте."""
    html = render_block({'type': 'heading', 'level': 'h2', 'text': 'Привет'})
    assert html.startswith('<div class="cb cb-heading"')
    assert '<h2>Привет</h2>' in html
    assert 'data-cb-anim' not in html
    text = render_block({'type': 'text', 'text': 'Абзац'})
    assert _norm(text) == _norm(
        '<div class="cb cb-text" style="text-align:left"><p>Абзац</p></div>')


# ---------- CN-T4: эффекты ----------

def test_effects_anim_attrs_and_assets():
    out = render_blocks([{'type': 'heading', 'text': 'А',
                          'effects': {'anim': 'slide-up', 'anim_duration': 800,
                                      'anim_delay': 200}}])
    assert 'cb-anim-slide-up' in out
    assert 'data-cb-anim="slide-up"' in out
    assert 'data-cb-anim-duration="800"' in out
    assert 'data-cb-anim-delay="200"' in out
    assert 'IntersectionObserver' in out          # ANIMATIONS_ASSETS подключён
    assert out.count('transition-timing-function:ease') == 1  # ассеты ровно один раз
    # невалидная анимация -> без класса и без ассетов
    out2 = render_blocks([{'type': 'heading', 'text': 'Б',
                           'effects': {'anim': 'hack'}}])
    assert 'cb-anim-' not in out2
    assert 'IntersectionObserver' not in out2
    # duration вне диапазона -> дефолт 600
    out3 = render_blocks([{'type': 'heading', 'text': 'В',
                           'effects': {'anim': 'fade', 'anim_duration': 99999}}])
    assert 'data-cb-anim-duration="600"' in out3


def test_effects_sticky_hidden_hide_on():
    out = render_blocks([
        {'type': 'heading', 'text': 'A', 'effects': {'sticky': True}},
        {'type': 'heading', 'text': 'B', 'effects': {'hide_on': 'mobile'}},
        {'type': 'heading', 'text': 'C', 'effects': {'hide_on': 'desktop'}},
        {'type': 'heading', 'text': 'D', 'hidden': True},
    ])
    assert 'cb-sticky' in out
    assert 'cb-hide-mobile' in out
    assert 'cb-hide-desktop' in out
    assert 'cb-hidden' in out
    assert '.cb-sticky' in out and '.cb-hidden' in out


def test_effects_decor_lines():
    html = render_block({'type': 'heading', 'text': 'Д',
                         'effects': {'decor_top': {'color': '#b49d84',
                                                   'thickness': 3, 'width': 60},
                                     'decor_bottom': {'color': '#000000',
                                                      'thickness': 1, 'width': 100}}})
    assert html.count('class="cb-decor') == 2
    assert 'cb-decor-top' in html and 'cb-decor-bottom' in html
    assert 'height:3px' in html and 'width:60%' in html
    # невалидный декор (без цвета) не рендерится
    bad = render_block({'type': 'heading', 'text': 'E',
                        'effects': {'decor_top': {'thickness': 3}}})
    assert 'cb-decor' not in bad


def test_css_class_valid_and_injection_rejected():
    html = render_block({'type': 'heading', 'text': 'F', 'css_class': 'my-block x_y'})
    assert 'my-block x_y' in html
    bad = render_block({'type': 'heading', 'text': 'G',
                        'css_class': '"><script>alert(1)</script>'})
    assert '<script' not in bad
    assert '&quot;&gt;' in bad or bad.find('script') == -1


def test_custom_css_superadmin_only_and_sanitized(app_context):
    import app as app_module
    # не суперадмин (вне запроса) — не рендерится
    html = render_block({'type': 'heading', 'text': 'H',
                         'custom_css': '.x{color:red}'})
    assert '<style>' not in html
    # суперадмин: безопасный CSS оборачивается в scoped .cb-uid
    with app_module.app.test_request_context():
        from flask import session
        session['admin_role'] = 'superadmin'
        html = render_block({'type': 'heading', 'text': 'H',
                             'custom_css': '.x{color:red}'})
    m = re.search(r'<style>\.cb-(u[a-z0-9]{10})\{\.x\{color:red\}\}</style>', html)
    assert m
    assert f'class="cb cb-heading' in html
    # суперадмин: @import / url( / <script> вырезаются санитайзером
    for payload in ('@import "https://evil/x.css";',
                    'body{background:url(https://evil/x.png)}',
                    'a{width:expression(alert(1))}',
                    'x{}</style><script>alert(1)</script>'):
        with app_module.app.test_request_context():
            from flask import session
            session['admin_role'] = 'superadmin'
            bad = render_block({'type': 'heading', 'text': 'I', 'custom_css': payload})
        assert '<style>' not in bad, payload
        assert '<script' not in bad, payload


# ---------- CN-T4: типографика ----------

def test_typography_elements():
    block = {'type': 'heading', 'text': 'Заголовок',
             'typography': {'title': {'size': '42px', 'weight': 'bold',
                                      'uppercase': True, 'letter_spacing': '2px',
                                      'line_height': '1.2', 'italic': True,
                                      'color': '#112233', 'align': 'center'}}}
    html = render_block(block)
    assert 'font-size:42px' in html
    assert 'font-weight:700' in html
    assert 'text-transform:uppercase' in html
    assert 'letter-spacing:2px' in html
    assert 'line-height:1.2' in html
    assert 'font-style:italic' in html
    assert 'color:#112233' in html
    assert 'text-align:center' in html


def test_typography_weight_medium_and_numeric():
    html = render_block({'type': 'text', 'text': 'Т',
                         'typography': {'text': {'weight': 'medium'}}})
    assert 'font-weight:500' in html
    html2 = render_block({'type': 'text', 'text': 'Т',
                          'typography': {'text': {'weight': 300}}})
    assert 'font-weight:300' in html2
    bad = render_block({'type': 'text', 'text': 'Т',
                        'typography': {'text': {'weight': 950}}})
    assert 'font-weight' not in bad


def test_typography_invalid_ignored():
    html = render_block({'type': 'text', 'text': 'Т',
                         'typography': {'text': {'size': '6px',        # ниже порога 8
                                                 'font': 'bad;font{x}',  # запрещённые символы
                                                 'color': 'red'}}})
    assert 'font-size' not in html
    assert 'font-family' not in html
    assert 'color' not in html


def test_google_fonts_link_once_for_two_blocks():
    blocks = [
        {'type': 'heading', 'text': 'А',
         'typography': {'title': {'font_google': 'Montserrat'}}},
        {'type': 'text', 'text': 'Б',
         'typography': {'text': {'font_google': 'Montserrat'}}},
        {'type': 'heading', 'text': 'В',
         'typography': {'title': {'font_google': 'Lora'}}},
    ]
    out = render_blocks(blocks)
    assert "font-family:'Montserrat',sans-serif" in out
    assert "font-family:'Lora',sans-serif" in out
    assert out.count('fonts.googleapis.com') == 1      # один link на страницу
    assert 'family=Montserrat' in out and 'family=Lora' in out
    # семейство не из белого списка не подключается
    out2 = render_blocks([{'type': 'heading', 'text': 'Г',
                           'typography': {'title': {'font_google': 'Evil Font'}}}] )
    assert 'fonts.googleapis.com' not in out2
    assert 'Evil Font' not in out2


# ---------- CN-T4: стили кнопок ----------

def test_button_style_outline():
    html = render_block({'type': 'button', 'text': 'Купить', 'url': '/catalog/',
                         'button_style': {'variant': 'outline', 'bg': '#b49d84',
                                          'radius': 12, 'shadow': 2,
                                          'hover': 'lift', 'size': 'l'}})
    assert 'background:transparent' in html
    assert 'border:2px solid #b49d84' in html
    assert 'border-radius:12px' in html
    assert 'box-shadow:0 6px 18px' in html
    assert 'data-cb-btn-hover="lift"' in html
    assert 'padding:0.9rem 2.2rem' in html      # size l


def test_button_style_fill_defaults_and_none_variant():
    html = render_block({'type': 'button', 'text': 'К', 'url': '/',
                         'button_style': {'variant': 'fill'}})
    assert 'background:#b49d84' in html      # дефолтный bg
    assert 'color:#ffffff' in html           # дефолтный color
    assert 'border-radius:6px' in html       # дефолтный radius
    html2 = render_block({'type': 'button', 'text': 'К', 'url': '/',
                          'button_style': {'variant': 'none', 'bg': '#123456'}})
    assert 'border:0' in html2
    assert 'color:#123456' in html2
    # старая разметка без button_style сохраняется
    old = render_block({'type': 'button', 'text': 'К', 'url': '/',
                        'color': '#ff0000'})
    assert 'background:#ff0000' in old
    assert 'data-cb-btn-hover' not in old


def test_button_style_sanitizes_url():
    html = render_block({'type': 'button', 'text': 'К',
                         'url': 'javascript:alert(1)',
                         'button_style': {'variant': 'fill'}})
    assert 'javascript:' not in html
    assert 'href="#"' in html


def test_button_style_on_form_timer_banner():
    form = render_block({'type': 'form', 'title': 'Ф', 'button': 'Отправить',
                         'button_style': {'variant': 'fill', 'bg': '#137333',
                                          'hover': 'darken'}})
    assert '<button type="submit" data-cb-btn-hover="darken"' in form
    assert 'background:#137333' in form
    timer = render_block({'type': 'timer', 'date': '2030-01-01',
                          'button_text': 'Успеть', 'button_link': '/akcii/',
                          'button_style': {'variant': 'outline'}})
    assert 'cb-timer-btn' in timer
    assert 'background:transparent' in timer
    banner = render_block({'type': 'banner', 'title': 'Б', 'button': 'Жми',
                           'url': '/x/', 'button_style': {'radius': 20}})
    assert 'cb-banner-btn' in banner and 'border-radius:20px' in banner


# ---------- CN-T8: zero-блок ----------

ZERO_BLOCK = {
    'type': 'zero', 'height': 600,
    'elements': [
        {'id': 'title1', 'type': 'text', 'x': 10, 'y': 5, 'w': 80, 'h': 20,
         'rotate': -3, 'z': 5, 'opacity': 90,
         'props': {'text': 'Заголовок zero',
                   'text_style': {'size': '36px', 'weight': 'bold'}}},
        {'id': 'btn1', 'type': 'button', 'x': 10, 'y': 40, 'w': 30, 'h': 12,
         'props': {'text': 'Кнопка', 'url': '/catalog/',
                   'button_style': {'variant': 'fill', 'bg': '#137333'}}},
        {'id': 'img1', 'type': 'image', 'x': 50, 'y': 30, 'w': 40, 'h': 50,
         'props': {'src': '/static/img/pic.png', 'alt': 'Картинка', 'radius': 12}},
        {'id': 'shape1', 'type': 'shape', 'x': 0, 'y': 0, 'w': 100, 'h': 100,
         'z': -1, 'props': {'shape': 'circle', 'color': '#eeeeee'}},
        {'id': 'video1', 'type': 'video', 'x': 5, 'y': 60, 'w': 40, 'h': 35,
         'props': {'url': 'https://youtu.be/dQw4w9WgXcQ'}},
        {'id': 'icon1', 'type': 'icon', 'x': 90, 'y': 5, 'w': 8, 'h': 10,
         'props': {'icon': '🚀', 'color': '#ff6600', 'size': 48}},
    ],
    'responsive': {
        'tablet': {'title1': {'x': 5, 'w': 90}, 'img1': {'hide': True}},
        'mobile': {'title1': {'x': 2, 'y': 2, 'w': 96, 'h': 30},
                   'btn1': {'hide': True}},
    },
}


def test_zero_block_positions_and_content():
    html = render_block(ZERO_BLOCK)
    assert 'cb-zero' in html
    assert 'height:600px' in html
    # позиции в процентах
    assert 'left:10%' in html and 'top:5%' in html
    assert 'width:80%' in html and 'height:20%' in html
    # rotate/z/opacity
    assert 'transform:rotate(-3deg)' in html
    assert 'z-index:5' in html
    assert 'opacity:0.9' in html
    # типы элементов
    assert 'Заголовок zero' in html
    assert 'font-size:36px' in html and 'font-weight:700' in html
    assert 'Кнопка' in html and 'background:#137333' in html
    assert 'src="/static/img/pic.png"' in html and 'border-radius:12px' in html
    assert 'border-radius:50%' in html                      # circle
    assert 'https://www.youtube.com/embed/dQw4w9WgXcQ' in html
    assert '🚀' in html and 'font-size:48px' in html


def test_zero_block_responsive_media_queries():
    html = render_block(ZERO_BLOCK)
    assert '@media (max-width:1024px)' in html
    assert '@media (max-width:640px)' in html
    assert 'left:5%' in html and 'width:90%' in html        # tablet title1
    assert 'display:none' in html                           # hide img1/btn1
    assert re.search(r'\.cb-zero-u[a-z0-9]{9,} \[data-el="title1"\]', html)
    assert re.search(r'\.cb-zero-u[a-z0-9]{9,} \[data-el="btn1"\]', html)


def test_zero_block_hidden_and_invalid_elements_skipped():
    block = {'type': 'zero', 'elements': [
        {'id': 'a', 'type': 'text', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
         'hidden': True, 'props': {'text': 'не покажется'}},
        {'id': 'b', 'type': 'unknown-type', 'x': 0, 'y': 0, 'w': 10, 'h': 10},
        {'id': 'bad id!', 'type': 'text', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
         'props': {'text': 'норм'}},
        {'id': 'c', 'type': 'image', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
         'props': {'src': 'javascript:alert(1)'}},
        {'id': 'd', 'type': 'icon', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
         'props': {'icon': '<img src=x onerror=alert(1)>'}},
        {'id': 'e', 'type': 'text', 'x': 150, 'y': 0, 'w': 10, 'h': 10,  # x>100
         'props': {'text': 'вне диапазона'}},
    ]}
    html = render_block(block)
    assert 'не покажется' not in html
    assert 'unknown-type' not in html
    assert 'javascript:' not in html
    assert 'onerror' not in html
    # x=150 не прошёл валидацию -> fallback к 0%, текст на месте
    assert 'вне диапазона' in html
    assert 'left:150%' not in html
    # элемент с невалидным id отрендерился с безопасным сгенерированным id
    assert 'норм' in html


def test_zero_block_image_src_quote_escaped():
    block = {'type': 'zero', 'elements': [
        {'id': 'i1', 'type': 'image', 'x': 0, 'y': 0, 'w': 10, 'h': 10,
         'props': {'src': '/img.png" onerror="alert(1)'}}]}
    html = render_block(block)
    # URL с кавычками/пробелами отсекается санитайзером целиком
    assert 'onerror' not in html
    assert 'img.png' not in html


def test_zero_block_height_default_and_validation():
    html = render_block({'type': 'zero', 'elements': []})
    assert 'height:500px' in html          # дефолт
    html2 = render_block({'type': 'zero', 'height': 50, 'elements': []})
    assert 'height:500px' in html2         # 50 < 100 -> дефолт
    html3 = render_block({'type': 'zero', 'height': 2000, 'elements': []})
    assert 'height:2000px' in html3


def test_zero_button_element_sanitizes_url():
    block = {'type': 'zero', 'elements': [
        {'id': 'b1', 'type': 'button', 'x': 0, 'y': 0, 'w': 20, 'h': 10,
         'props': {'text': 'X', 'url': 'javascript:alert(1)'}}]}
    html = render_block(block)
    assert 'javascript:' not in html
    assert 'href="#"' in html


def test_zero_link_wraps_element():
    block = {'type': 'zero', 'elements': [
        {'id': 't1', 'type': 'text', 'x': 0, 'y': 0, 'w': 20, 'h': 10,
         'link': 'https://example.com/', 'props': {'text': 'Ссылка'}}]}
    html = render_block(block)
    assert '<a href="https://example.com/"' in html
    assert 'Ссылка' in html
    bad = render_block({'type': 'zero', 'elements': [
        {'id': 't2', 'type': 'text', 'x': 0, 'y': 0, 'w': 20, 'h': 10,
         'link': 'javascript:alert(1)', 'props': {'text': 'X'}}]})
    assert 'javascript:' not in bad


# ---------- Обратная совместимость ----------

LEGACY_BLOCKS = [
    {'type': 'heading', 'level': 2, 'text': 'Заголовок'},
    {'type': 'text', 'text': 'Просто текст.'},
    {'type': 'image', 'src': '/static/img/x.png', 'alt': 'картинка'},
    {'type': 'button', 'text': 'Купить', 'url': '/catalog/'},
    {'type': 'video', 'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'},
    {'type': 'divider'},
    {'type': 'reviews', 'items': [{'name': 'Анна', 'text': 'Отлично'}]},
    {'type': 'form', 'title': 'Заказать звонок'},
    {'type': 'map', 'address': 'Москва'},
    {'type': 'faq', 'items': [{'q': 'Вопрос?', 'a': 'Ответ.'}]},
    {'type': 'banner', 'title': 'Баннер'},
    {'type': 'timer', 'date': '2030-01-01', 'title': 'Акция'},
    {'type': 'text_image', 'text': 'Текст', 'src': '/static/img/x.png'},
    {'type': 'gallery', 'items': [{'src': '/static/img/a.png', 'alt': 'а'}]},
    {'type': 'features', 'items': [{'title': 'Ф1', 'text': 'Описание'}]},
    {'type': 'table', 'header_row': True, 'items': [
        {'cells': 'A | B'}, {'cells': ['1', '2']}]},
    {'type': 'spacer', 'height': 40},
    {'type': 'text_columns', 'items': [{'text': 'К1'}, {'text': 'К2'}]},
    {'type': 'contacts', 'phone': '+7 900 000-00-00'},
]


def test_legacy_blocks_renders_key_fragments():
    out = render_blocks(LEGACY_BLOCKS)
    for fragment in ('cb-heading', '<h2>Заголовок</h2>', 'cb-text', 'cb-image',
                     'cb-button', 'Купить', 'cb-video', 'cb-divider',
                     'cb-review-card', 'Анна', 'cb-form', 'Заказать звонок',
                     'cb-map', 'cb-faq', 'cb-banner', 'cb-banner-inner',
                     'cb-timer', 'data-cb-timer-date="2030-01-01"',
                     'cb-ti-grid', 'cb-gallery-grid', 'cb-features-grid',
                     'cb-table-wrap', 'cb-spacer', 'height:40px',
                     'cb-cols-grid', 'cb-contacts-card'):
        assert fragment in out, fragment
    # гидратор таймера подключён, анимационные ассеты — нет
    assert 'setInterval' in out
    assert 'IntersectionObserver' not in out
    assert 'fonts.googleapis.com' not in out
    # суперадминский html-блок вне запроса — заглушка (как раньше)
    assert render_block({'type': 'html', 'html': '<b>x</b>'}) == \
        '<!-- html block: restricted -->'


def test_block_column_classes_respected():
    # gallery
    out = render_block({'type': 'gallery', 'cols': 2,
                        'items': [{'src': '/a.jpg'}, {'src': '/b.jpg'}]})
    assert 'cb-gallery-c2' in out
    assert 'cb-gallery-c3' not in out
    # features
    out = render_block({'type': 'features', 'cols': 3,
                        'items': [{'title': 'A', 'text': 'b'}]})
    assert 'cb-features-c3' in out
    assert 'cb-features-c4' not in out
    # text_columns: по умолчанию по количеству пунктов
    out = render_block({'type': 'text_columns',
                        'items': [{'text': '1'}, {'text': '2'}, {'text': '3'}]})
    assert 'cb-cols-3' in out
    # text_columns: явный cols валидируется и не превышает число пунктов
    out = render_block({'type': 'text_columns', 'cols': '4',
                        'items': [{'text': '1'}, {'text': '2'}, {'text': '3'}]})
    assert 'cb-cols-3' in out
    out = render_block({'type': 'text_columns', 'cols': '2',
                        'items': [{'text': '1'}, {'text': '2'}, {'text': '3'}]})
    assert 'cb-cols-2' in out


def test_render_blocks_marked_still_marks():
    out = blocks_module.render_blocks_marked(
        [{'type': 'heading', 'text': 'М'}, {'type': 'divider'}])
    assert '<!--blk:0-->' in out and '<!--/blk-->' in out
    assert '<h2>М</h2>' in out
    assert '<!--blk:1-->' in out


def test_render_blocks_non_list_safe():
    assert render_blocks(None) == ''
    assert render_blocks('x') == ''
    assert render_blocks([None, 'x', {'type': 'nope'}]).strip() == ''

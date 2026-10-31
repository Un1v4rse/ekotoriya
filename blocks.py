#!/usr/bin/env python3
"""
Визуальный конструктор страниц: рендерит массив блоков (JSON) в HTML.

Блоки — это то, что админ редактирует в админке без кода:
    heading   — заголовок (text, level: h1|h2|h3)
    text      — текст (text, абзацы через пустую строку)
    image     — картинка (src, alt, caption, upload)
    button    — кнопка (text, url, color, align)
    video     — видео YouTube/Vimeo (url)
    products  — товары WB (source: wb|all, limit, title)
    reviews   — отзывы покупателей (title, items: [{name, text, rating 1-5}])
    form      — форма обратной связи (title, button, recipient)
    map       — Яндекс.Карта по адресу (title, address)
    faq       — вопросы-ответы (title, items: [{q, a}])
    banner    — промо-баннер (title, subtitle, button, url, bg)
    divider   — разделитель
    html      — произвольный HTML (для перенесённого дизайна, редактирование по желанию)
    zero      — zero-блок (CN-T8): абсолютно позиционированные элементы
                (text/button/image/shape/video/icon) + адаптив tablet/mobile

Общие поля блока (цикл 2, CN-T4): фон (bg_type/bg_image/bg_gradient_*/bg_video/
bg_opacity), отступы (padding_top/padding_bottom), container theme|full,
min_height, content_align, эффекты (effects: anim/sticky/decor/hide_on),
css_class, custom_css (только суперадмин), hidden. Старые поля bg/padding/
text_color работают как раньше. Типографика элементов — block['typography']
{title, subtitle, text, caption, button, item_title, item_text}, кнопки —
block['button_style']. Всё валидируется, невалидное игнорируется.

Страница с ключом 'blocks' рендерится из блоков; content_html генерируется
автоматически при сохранении в админке (app.py /admin/api/save).
"""
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs, quote


def esc(value):
    return html.escape(str(value or ''), quote=True)


# HTML-санитайзер (Этап 6.1): второй слой защиты для блока «HTML-код».
# Пропускает только безопасные теги; script/style/iframe вырезаются целиком
# вместе с содержимым; из остальных тегов удаляются on*-атрибуты и
# javascript:/data:-URL. Реализован на stdlib html.parser (без зависимостей).
_SANITIZE_ALLOWED_TAGS = {
    'a', 'b', 'i', 'em', 'strong', 'u', 's', 'p', 'br',
    'ul', 'ol', 'li', 'div', 'span',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'img',
}
_SANITIZE_DROP_WITH_CONTENT = {'script', 'style', 'iframe', 'object', 'embed'}
_SANITIZE_ALLOWED_ATTRS = {
    'a': {'href', 'title', 'target', 'rel'},
    'img': {'src', 'alt', 'title', 'width', 'height'},
    '*': {'class', 'id', 'title', 'align'},
}


def _sanitize_url(value):
    """Разрешить только http(s): и относительные ссылки; иначе пустая строка."""
    value = (value or '').strip()
    if not value:
        return ''
    low = value.lower()
    if low.startswith(('javascript:', 'data:', 'vbscript:')):
        return ''
    # кавычки/скобки/пробелы могут вырваться из атрибута — отсекаем целиком
    if any(c in value for c in '<>"\'\t\n\r '):
        return ''
    if low.startswith(('http://', 'https://', '/', '#', 'mailto:', 'tel:')):
        return value
    return ''


class _SanitizeHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self._drop_stack = []   # вложенность вырезаемых тегов (script/style/iframe)

    def _dropping(self):
        return bool(self._drop_stack)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SANITIZE_DROP_WITH_CONTENT:
            self._drop_stack.append(tag)
            return
        if self._dropping() or tag not in _SANITIZE_ALLOWED_TAGS:
            return
        allowed = _SANITIZE_ALLOWED_ATTRS.get(tag, set()) | _SANITIZE_ALLOWED_ATTRS['*']
        clean = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith('on') or name not in allowed:
                continue
            if name in ('href', 'src'):
                value = _sanitize_url(value)
                if not value:
                    continue
            if name == 'target' and value not in ('_blank', '_self'):
                continue
            if name in ('width', 'height') and not re.fullmatch(r'\d{1,4}', str(value or '')):
                continue
            clean.append(f'{name}="{esc(value)}"' if value is not None else name)
        suffix = ' ' + ' '.join(clean) if clean else ''
        if tag in ('br', 'img'):
            self.out.append(f'<{tag}{suffix}>')
        else:
            self.out.append(f'<{tag}{suffix}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._drop_stack:
            if tag == self._drop_stack[-1]:
                self._drop_stack.pop()
            return
        if tag in _SANITIZE_ALLOWED_TAGS and tag not in ('br', 'img'):
            self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if not self._dropping():
            self.out.append(esc(data))

    def handle_entityref(self, name):
        if not self._dropping():
            self.out.append(f'&{name};')

    def handle_charref(self, name):
        if not self._dropping():
            self.out.append(f'&#{name};')

    def handle_comment(self, data):
        pass   # комментарии не пропускаем


def _sanitize_html(raw):
    """Очистить произвольный HTML до безопасного подмножества тегов."""
    parser = _SanitizeHTMLParser()
    try:
        parser.feed(str(raw or ''))
        parser.close()
    except Exception:
        return ''
    return ''.join(parser.out)


def _is_superadmin():
    """True, если текущий запрос от суперадмина (для блока «HTML-код», CN-2)."""
    try:
        from flask import has_request_context, session
        if not has_request_context():
            return False
        return session.get('admin_role') == 'superadmin'
    except Exception:
        return False


def _youtube_id(url):
    """Извлечь id ролика YouTube из разных форматов ссылок."""
    if not url:
        return ''
    m = re.search(r'(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([\w\-]{6,20})', url)
    if m:
        return m.group(1)
    return ''


def _vimeo_id(url):
    if not url:
        return ''
    m = re.search(r'vimeo\.com/(\d+)', url)
    return m.group(1) if m else ''


def _video_embed_url(url):
    """Безопасный embed-URL для фонового/zero-видео: YouTube/Vimeo/RuTube."""
    url = (url or '').strip()
    if not url.lower().startswith(('http://', 'https://')):
        return ''
    yt = _youtube_id(url)
    if yt:
        return f'https://www.youtube.com/embed/{yt}'
    vm = _vimeo_id(url)
    if vm:
        return f'https://player.vimeo.com/video/{vm}'
    m = re.search(r'rutube\.ru/(?:video/|play/embed/)([a-zA-Z0-9]+)', url)
    if m:
        return f'https://rutube.ru/play/embed/{m.group(1)}'
    return ''


def _parse_items(raw):
    """Нормализовать список элементов блока: список словарей или JSON-строка."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [it for it in (raw or []) if isinstance(it, dict)]


def _stars(rating):
    """Строка звёзд из оценки 1-5."""
    try:
        rating = max(1, min(5, int(rating)))
    except (TypeError, ValueError):
        rating = 5
    return '★' * rating + '☆' * (5 - rating)


def _color(value, default=''):
    """Валидация hex-цвета; неподходящее значение -> default."""
    value = (value or '').strip()
    return value if re.fullmatch(r'#[0-9a-fA-F]{3,8}', value) else default


def _px(value, lo, hi):
    """Целое число в диапазоне [lo, hi]; иначе None (невалидное игнорируем)."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _pct(value):
    """Процент 0-100 (до 2 знаков после точки) строкой; иначе None."""
    value = str(value or '').strip()
    if not re.fullmatch(r'\d{1,3}(\.\d{1,2})?', value):
        return None
    return value if 0 <= float(value) <= 100 else None


def _style_attr(block):
    """Инлайн-стили контейнера блока: фон, вертикальные отступы, цвет текста.

    Оставлено для совместимости (блок «Секция»); остальные типы проходят
    через _block_wrapper, который покрывает те же поля без двойного применения.
    """
    styles = []
    bg = _color(block.get('bg'))
    if bg:
        styles.append(f'background:{bg}')
    padding = str(block.get('padding', '')).strip()
    if re.fullmatch(r'\d{1,3}', padding):
        styles.append(f'padding-top:{padding}px')
        styles.append(f'padding-bottom:{padding}px')
    color = _color(block.get('text_color'))
    if color:
        styles.append(f'color:{color}')
    return f' style="{";".join(styles)}"' if styles else ''


# Допустимые Google Fonts (кириллические семейства, CN-T4 типографика).
_GOOGLE_FONTS = (
    'Roboto', 'Open Sans', 'Montserrat', 'Ubuntu', 'PT Sans', 'PT Serif',
    'Lora', 'Playfair Display', 'Inter', 'Fira Sans', 'Oswald', 'Rubik',
    'Nunito', 'Noto Sans', 'Manrope', 'Bebas Neue', 'Comfortaa', 'Jost',
    'Mulish', 'Source Sans 3', 'Source Serif 4', 'IBM Plex Sans',
    'IBM Plex Serif', 'Golos Text', 'Exo 2', 'Tinos', 'Cormorant Garamond',
    'Lobster', 'Pacifico', 'Neucha',
)
_GOOGLE_FONTS_SET = frozenset(_GOOGLE_FONTS)


def _typo_styles(cfg):
    """Строка инлайн-стилей из конфига типографики; пусто -> дефолты темы."""
    if not isinstance(cfg, dict):
        return ''
    styles = []
    fam = str(cfg.get('font') or '').strip()
    if fam and re.fullmatch(r'[^{}<>;]{1,100}', fam):
        styles.append(f'font-family:{fam}')
    gf = str(cfg.get('font_google') or '').strip()
    if gf in _GOOGLE_FONTS_SET:
        # Семейство из белого списка — безопасно подставлять без esc
        styles.append(f"font-family:'{gf}',sans-serif")
    size = str(cfg.get('size') or '').strip()
    m = re.fullmatch(r'(\d{1,3})(px|rem)', size)
    if m and 8 <= int(m.group(1)) <= 120:
        styles.append(f'font-size:{size}')
    color = _color(cfg.get('color'))
    if color:
        styles.append(f'color:{color}')
    lh = str(cfg.get('line_height') or '').strip()
    if re.fullmatch(r'\d{1,2}(\.\d{1,2})?', lh) and 0.8 <= float(lh) <= 3:
        styles.append(f'line-height:{lh}')
    ls = str(cfg.get('letter_spacing') or '').strip()
    m = re.fullmatch(r'(-?\d{1,2}(\.\d{1,2})?)(px|em)', ls)
    if m and -5 <= float(m.group(1)) <= 20:
        styles.append(f'letter-spacing:{ls}')
    weight = str(cfg.get('weight') or '').strip().lower()
    if weight in ('normal', 'medium', 'bold'):
        styles.append(f"font-weight:{'normal' if weight == 'normal' else '500' if weight == 'medium' else '700'}")
    else:
        wn = _px(cfg.get('weight'), 100, 900)
        if wn is not None:
            styles.append(f'font-weight:{wn}')
    if cfg.get('italic') is True:
        styles.append('font-style:italic')
    if cfg.get('uppercase') is True:
        styles.append('text-transform:uppercase')
    align = str(cfg.get('align') or '').strip()
    if align in ('left', 'center', 'right', 'justify'):
        styles.append(f'text-align:{align}')
    return ';'.join(styles)


def _typo(block, element):
    """Типографика элемента блока: block['typography'][element] -> стили."""
    typo = block.get('typography')
    if not isinstance(typo, dict):
        return ''
    return _typo_styles(typo.get(element))


def _div(style, content):
    """Обернуть контент в div со стилем, если стиль непустой."""
    return f'<div style="{style}">{content}</div>' if style else content


# Стили кнопок (CN-T4): варианты fill/outline/none, тени, ховер, размеры.
_BTN_SIZES = {
    's': ('0.4rem 1rem', '0.85rem'),
    'm': ('0.7rem 1.6rem', '1rem'),
    'l': ('0.9rem 2.2rem', '1.15rem'),
}
_BTN_SHADOWS = {
    0: '',
    1: 'box-shadow:0 2px 6px rgba(0,0,0,0.18)',
    2: 'box-shadow:0 6px 18px rgba(0,0,0,0.28)',
}


# Встроенный набор SVG-иконок конструктора (цикл 4 / этап 5).
# Использование в поле иконки: icon:<имя> — например icon:truck.
# Рисуем stroke'ом currentColor: иконка принимает цвет текста блока.
_SVG_ICONS = {
    'truck': '<rect x="1" y="4" width="14" height="11" rx="1"/><path d="M15 8h4l3 3v4h-7V8z"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="17.5" cy="18.5" r="2.2"/>',
    'shield': '<path d="M12 2l8 3v6c0 5-3.5 9.3-8 11-4.5-1.7-8-6-8-11V5l8-3z"/><path d="M9 12l2 2 4-4"/>',
    'percent': '<path d="M19 5L5 19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>',
    'chat': '<path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5z"/><path d="M9 11h.01M13 11h.01M17 11h.01"/>',
    'star': '<path d="M12 2l3 6.5 7 .8-5.2 4.7 1.5 6.9L12 17.5 5.7 20.9l1.5-6.9L2 9.3l7-.8L12 2z"/>',
    'heart': '<path d="M12 20.5C7 16.5 3 13.3 3 9.3 3 6.4 5.2 4.5 7.7 4.5c1.7 0 3.3.9 4.3 2.4 1-1.5 2.6-2.4 4.3-2.4 2.5 0 4.7 1.9 4.7 4.8 0 4-4 7.2-9 11.2z"/>',
    'phone': '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.13.96.36 1.9.7 2.8a2 2 0 0 1-.45 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.45c.9.34 1.84.57 2.8.7A2 2 0 0 1 22 16.9z"/>',
    'mail': '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 7l-10 6L2 7"/>',
    'check': '<path d="M20 6L9 17l-5-5"/>',
    'arrow-right': '<path d="M5 12h14M13 6l6 6-6 6"/>',
    'box': '<path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
    'gift': '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'map-pin': '<path d="M12 21s-7-5.5-7-11a7 7 0 0 1 14 0c0 5.5-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    'wallet': '<path d="M20 7H4a2 2 0 0 1 0-4h14v4"/><path d="M20 7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5"/><circle cx="17" cy="13.5" r="1.2"/>',
    'award': '<circle cx="12" cy="9" r="6"/><path d="M8.5 14L7 22l5-3 5 3-1.5-8"/>',
    'refresh': '<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>',
    'factory': '<path d="M2 20h20M4 20V9l5 3V9l5 3V9l5 3v8"/><path d="M17 5V2h3v6"/>',
    'leaf': '<path d="M6 21c0-9 4-15 14-16-1 10-7 14-14 16z"/><path d="M6 21c3-6 7-9 11-11"/>',
}

SVG_ICON_NAMES = sorted(_SVG_ICONS.keys())


def _svg_icon_html(name, size=48):
    """Inline-SVG встроенной иконки набора; '' если имени нет."""
    body = _SVG_ICONS.get((name or '').strip().lower())
    if not body:
        return ''
    try:
        size = max(12, min(120, int(size)))
    except (TypeError, ValueError):
        size = 48
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>')


def _icon_html(value, size=48):
    """Иконка по значению поля: icon:<имя> — SVG из набора; http(s):// или
    /путь — <img> (своя загрузка через медиатеку); иначе — эмодзи/текст."""
    value = (value or '').strip()
    if not value:
        return ''
    if value.startswith('icon:'):
        return _svg_icon_html(value[5:], size)
    if re.fullmatch(r'[\w\-. /:%?&=]+', value) and ('/' in value or value.startswith('http')):
        return f'<img class="cb-feature-icon" src="{esc(value)}" alt="" loading="lazy">'
    if len(value) <= 4 and '<' not in value and '>' not in value:
        return f'<div style="font-size:{max(16, min(96, size))}px;line-height:1;">{esc(value)}</div>'
    return ''


def _button_style(block, key='button_style'):
    """Inline-стили и data-атрибуты кнопки из block['button_style'].

    Возвращает (style, extra_attrs); если настройки нет — ('', '').
    """
    cfg = block.get(key)
    if not isinstance(cfg, dict):
        return '', ''
    variant = str(cfg.get('variant') or 'fill')
    if variant not in ('fill', 'outline', 'none'):
        variant = 'fill'
    bg = _color(cfg.get('bg'), '#b49d84')
    color = _color(cfg.get('color'), '#ffffff')
    border = _color(cfg.get('border_color'), bg)
    radius = _px(cfg.get('radius'), 0, 60)
    radius = 6 if radius is None else radius
    shadow = _px(cfg.get('shadow'), 0, 2)
    shadow = 0 if shadow is None else shadow
    size = str(cfg.get('size') or 'm')
    if size not in _BTN_SIZES:
        size = 'm'
    pad, fsize = _BTN_SIZES[size]
    styles = [
        f'border-radius:{radius}px', f'padding:{pad}', f'font-size:{fsize}',
        'display:inline-block', 'text-decoration:none', 'cursor:pointer',
        'font-weight:600', 'text-align:center',
    ]
    if variant == 'fill':
        styles += [f'background:{bg}', f'color:{color}', f'border:2px solid {border}']
    elif variant == 'outline':
        styles += ['background:transparent', f'color:{border}', f'border:2px solid {border}']
    else:
        styles += ['background:transparent', f'color:{bg}', 'border:0',
                   'padding-left:0', 'padding-right:0']
    if _BTN_SHADOWS[shadow]:
        styles.append(_BTN_SHADOWS[shadow])
    hover = str(cfg.get('hover') or 'none')
    if hover not in ('darken', 'lift', 'none'):
        hover = 'none'
    attrs = f' data-cb-btn-hover="{hover}"' if hover != 'none' else ''
    return ';'.join(styles), attrs


_BG_POSITIONS = {'left top', 'center top', 'right top',
                 'left center', 'center', 'right center',
                 'left bottom', 'center bottom', 'right bottom'}
_ANIMS = ('fade', 'slide-up', 'slide-left', 'slide-right', 'zoom')


def _block_uid(btype, inner_html):
    """Стабильный безопасный uid [a-z0-9] для scoped custom_css."""
    digest = hashlib.md5(f'{btype}|{inner_html}'.encode('utf-8')).hexdigest()
    return 'u' + digest[:10]


def _decor_line(cfg, cls):
    """Декоративная линия сверху/снизу блока; пусто при невалидных данных."""
    if not isinstance(cfg, dict):
        return ''
    color = _color(cfg.get('color'))
    thickness = _px(cfg.get('thickness'), 1, 20)
    if not color or thickness is None:
        return ''
    width = _px(cfg.get('width'), 1, 100)
    width = 100 if width is None else width
    return (f'<div class="{cls}" style="background:{color};height:{thickness}px;'
            f'width:{width}%;margin:0 auto;"></div>')


def _block_wrapper(block, inner_html, uid=None, extra_style='', extra_attrs='', legacy=True):
    """Общая обёртка блока (CN-T4): фон, отступы, расположение, эффекты.

    legacy=True — старые поля bg/padding/text_color читаются как раньше
    (bg -> фон-цвет). Без новых полей разметка совпадает с прежней.
    """
    btype = re.sub(r'[^a-z0-9\-]', '', str(block.get('type') or 'text'))[:40] or 'text'
    if not uid:
        uid = _block_uid(btype, inner_html)
    classes = [f'cb-{btype}']
    styles = []
    attrs = extra_attrs

    # --- фон ---
    bg_type = str(block.get('bg_type') or '').strip()
    if bg_type not in ('', 'color', 'image', 'gradient', 'video', 'none'):
        bg_type = ''
    bg_html = ''
    if bg_type == 'image':
        img = _sanitize_url(block.get('bg_image'))
        if img:
            pos = str(block.get('bg_image_position') or 'center')
            pos = pos if pos in _BG_POSITIONS else 'center'
            size = str(block.get('bg_image_size') or 'cover')
            size = size if size in ('cover', 'contain', 'auto') else 'cover'
            repeat = str(block.get('bg_image_repeat') or 'no-repeat')
            repeat = repeat if repeat in ('repeat', 'no-repeat') else 'no-repeat'
            styles.append(f'background:url("{esc(img)}") {pos}/{size} {repeat}')
            if block.get('bg_parallax') is True:
                classes.append('cb-bg-parallax')
    elif bg_type == 'gradient':
        c1 = _color(block.get('bg_gradient_from'))
        c2 = _color(block.get('bg_gradient_to'))
        if c1 and c2:
            angle = _px(block.get('bg_gradient_angle'), 0, 360)
            angle = 180 if angle is None else angle
            styles.append(f'background:linear-gradient({angle}deg,{c1},{c2})')
    elif bg_type == 'video':
        embed = _video_embed_url(block.get('bg_video'))
        if embed:
            overlay = _px(block.get('bg_opacity'), 0, 100)
            overlay = 40 if overlay is None else overlay
            bg_html = (
                '<div class="cb-bg-video" style="position:absolute;inset:0;overflow:hidden;z-index:0;">'
                f'<iframe src="{embed}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;"'
                ' allowfullscreen loading="lazy" tabindex="-1" title="Фоновое видео"></iframe>'
                f'<div style="position:absolute;inset:0;background:rgba(0,0,0,{overlay / 100:g});"></div></div>')
            styles.append('position:relative')
            inner_html = f'<div style="position:relative;z-index:1;">{inner_html}</div>'
    elif bg_type in ('', 'color'):
        col = _color(block.get('bg'))
        if col and legacy:
            styles.append(f'background:{col}')

    # --- расположение ---
    pt = _px(block.get('padding_top'), 0, 200)
    pb = _px(block.get('padding_bottom'), 0, 200)
    if pt is None and pb is None:
        pad = _px(block.get('padding'), 0, 999)
        if pad is not None:
            styles.append(f'padding-top:{pad}px')
            styles.append(f'padding-bottom:{pad}px')
    else:
        if pt is not None:
            styles.append(f'padding-top:{pt}px')
        if pb is not None:
            styles.append(f'padding-bottom:{pb}px')
    if legacy:
        tc = _color(block.get('text_color'))
        if tc:
            styles.append(f'color:{tc}')
    min_h = _px(block.get('min_height'), 0, 1200)
    if min_h is not None:
        styles.append(f'min-height:{min_h}px')
    if 'align' not in block:
        ca = str(block.get('content_align') or '').strip()
        if ca in ('left', 'center', 'right'):
            styles.append(f'text-align:{ca}')
    if str(block.get('container') or 'theme') == 'full':
        classes.append('cb-container-full')

    # --- эффекты ---
    fx = block.get('effects')
    fx = fx if isinstance(fx, dict) else {}
    anim = str(fx.get('anim') or 'none')
    if anim in _ANIMS:
        classes.append(f'cb-anim-{anim}')
        dur = _px(fx.get('anim_duration'), 200, 2000)
        dly = _px(fx.get('anim_delay'), 0, 2000)
        attrs += (f' data-cb-anim="{anim}"'
                  f' data-cb-anim-duration="{600 if dur is None else dur}"'
                  f' data-cb-anim-delay="{0 if dly is None else dly}"')
    if fx.get('sticky') is True:
        classes.append('cb-sticky')
    hide_on = str(fx.get('hide_on') or 'none')
    if hide_on == 'mobile':
        classes.append('cb-hide-mobile')
    elif hide_on == 'desktop':
        classes.append('cb-hide-desktop')
    decor_top = _decor_line(fx.get('decor_top'), 'cb-decor cb-decor-top')
    decor_bottom = _decor_line(fx.get('decor_bottom'), 'cb-decor cb-decor-bottom')
    if block.get('hidden') is True:
        classes.append('cb-hidden')

    # --- дополнительно ---
    css_class = str(block.get('css_class') or '').strip()
    if css_class and re.fullmatch(r'[a-zA-Z0-9_\- ]{0,60}', css_class):
        classes.append(css_class)
    custom_css_html = ''
    raw_css = block.get('custom_css')
    if isinstance(raw_css, str) and _is_superadmin():
        css = raw_css.strip()[:2000]
        low = css.lower()
        forbidden = ('@import', 'url(', 'expression(', 'behavior', '<', '>')
        if css and not any(t in low for t in forbidden):
            custom_css_html = f'<style>.cb-{uid}{{{css}}}</style>'

    if extra_style:
        styles.insert(0, extra_style.rstrip(';'))
    style_attr = f' style="{";".join(styles)}"' if styles else ''
    body = f'{decor_top}{bg_html}{inner_html}{decor_bottom}'
    return (f'<div class="cb {" ".join(classes)}"{attrs}{style_attr}>'
            f'{body}</div>{custom_css_html}')


def _load_products():
    """Видимые товары всех источников с итоговыми ценами (для блока «Товары»)."""
    from db import db
    from models import Product
    rows = db.session.query(Product).filter_by(visible=True).order_by(Product.id).all()
    products = [p.to_product_dict() for p in rows]
    import pricing
    return pricing.enrich_products(products)


def _product_cards_html(products):
    """Компактные карточки товаров для блока «Товары» (классы как в каталоге —
    кнопки лайк/1-клик подхватываются существующим wb-actions.js)."""
    out = []
    for p in products:
        pid = p.get('id') or p.get('nm_id')
        photo = p.get('photo') or ''
        img = photo if photo.startswith('/') else f'/proxy/image?url={quote(photo)}'
        stock = p.get('stock', 0)
        final = p.get('final_price') or p.get('price') or 0
        badge = (f'<span style="background:#137333;color:#fff;font-size:0.72rem;'
                 f'padding:0.15rem 0.5rem;border-radius:4px;">скидка '
                 f'{p.get("site_discount_badge", 0)}% на сайте</span>'
                 if p.get('site_discount_badge') else '')
        stock_html = ('<div style="margin-top:0.5rem;font-size:0.82rem;font-weight:600;color:#c00;">Нет в наличии</div>'
                      if stock <= 0 else '')
        out.append(
            f'<a href="/catalog/{p.get("source", "wb")}/{pid}/" class="wb-product-card" '
            f'style="display:flex;flex-direction:column;border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;'
            f'background:#fff;text-decoration:none;color:inherit;">'
            f'<div style="padding:1rem;background:#f9f9f9;">'
            f'<img src="{img}" alt="{esc(p.get("name", ""))}" loading="lazy" '
            f'style="width:100%;height:220px;object-fit:contain;display:block;"></div>'
            f'<div style="padding:1rem;display:flex;flex-direction:column;flex:1 0 auto;">'
            f'<div style="font-weight:600;font-size:0.95rem;line-height:1.35;min-height:3.9em;'
            f'overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;">'
            f'{esc(p.get("name", ""))}</div>'
            f'<div style="margin-top:0.75rem;display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;">'
            f'<span style="color:#c00;font-weight:700;font-size:1.15rem;">{final} ₽</span>{badge}</div>'
            f'{stock_html}'
            f'<button type="button" class="wb-like-btn" data-id="{pid}" data-name="{esc(p.get("name", ""))}" '
            f'style="margin-top:auto;width:100%;background:#fff;color:#c00;border:1px solid #c00;'
            f'padding:0.5rem;border-radius:6px;cursor:pointer;font-weight:600;">'
            f'<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:-2px;margin-right:2px;">'
            f'<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
            f' В избранное</button>'
            f'<button type="button" class="wb-buy-btn" data-id="{pid}" data-name="{esc(p.get("name", ""))}" '
            f'data-price="{final}" data-stock="{stock}" '
            f'style="margin-top:0.5rem;width:100%;background:#b49d84;color:#fff;border:0;padding:0.6rem;'
            f'border-radius:6px;cursor:pointer;font-weight:600;"'
            f'{" disabled" if stock <= 0 else ""}>Купить в 1 клик</button>'
            f'</div></a>')
    return ''.join(out)


def _zero_element_html(el, index):
    """Один элемент zero-блока: абсолютное позиционирование в процентах."""
    if not isinstance(el, dict):
        return ''
    etype = str(el.get('type') or '')
    if etype not in ('text', 'button', 'image', 'shape', 'video', 'icon'):
        return ''
    if el.get('hidden') is True:
        return ''
    eid = str(el.get('id') or f'el{index}')
    if not re.fullmatch(r'[a-z0-9\-]{1,40}', eid):
        eid = f'el{index}'
    x = _pct(el.get('x'))
    y = _pct(el.get('y'))
    w = _pct(el.get('w'))
    h = _pct(el.get('h'))
    styles = [f'left:{x or 0}%', f'top:{y or 0}%',
              f'width:{w or 20}%', f'height:{h or 10}%']
    rotate = _px(el.get('rotate'), -180, 180)
    if rotate:
        styles.append(f'transform:rotate({rotate}deg)')
    z = _px(el.get('z'), 0, 100)
    if z is not None:
        styles.append(f'z-index:{z}')
    opacity = _px(el.get('opacity'), 1, 100)
    if opacity is not None and opacity != 100:
        styles.append(f'opacity:{opacity / 100:g}')
    props = el.get('props')
    props = props if isinstance(props, dict) else {}

    if etype == 'text':
        content = esc(props.get('text') or '')
        content = _div('width:100%;height:100%;overflow:hidden;' +
                       _typo_styles(props.get('text_style')), content)
    elif etype == 'button':
        text = (props.get('text') or '').strip() or 'Подробнее'
        url = _sanitize_url(props.get('url')) or '#'
        bstyle, battrs = _button_style({'button_style': props.get('button_style')})
        if not bstyle:
            bstyle = ('display:inline-block;background:#b49d84;color:#fff;'
                      'padding:0.6rem 1.4rem;border-radius:6px;text-decoration:none;'
                      'font-weight:600;border:2px solid #b49d84')
        content = f'<a href="{esc(url)}"{battrs} style="{bstyle}">{esc(text)}</a>'
    elif etype == 'image':
        src = _sanitize_url(props.get('src'))
        if not src:
            return ''
        radius = _px(props.get('radius'), 0, 60)
        radius_css = f'border-radius:{radius}px;' if radius is not None else ''
        content = (f'<img src="{esc(src)}" alt="{esc(props.get("alt") or "")}" loading="lazy" '
                   f'style="width:100%;height:100%;object-fit:cover;display:block;{radius_css}">')
    elif etype == 'shape':
        color = _color(props.get('color'), '#b49d84')
        shape = str(props.get('shape') or 'rect')
        if shape == 'circle':
            css = f'width:100%;height:100%;background:{color};border-radius:50%;'
        elif shape == 'line':
            css = f'width:100%;height:100%;min-height:2px;background:{color};'
        else:
            css = f'width:100%;height:100%;background:{color};'
        content = f'<div style="{css}"></div>'
    elif etype == 'video':
        embed = _video_embed_url(props.get('url'))
        if not embed:
            return ''
        content = (f'<iframe src="{embed}" style="width:100%;height:100%;border:0;" '
                   f'allowfullscreen loading="lazy" title="Видео"></iframe>')
    else:  # icon
        icon = str(props.get('icon') or '').strip()
        if not icon:
            return ''
        color = _color(props.get('color'), '#333333')
        size = _px(props.get('size'), 4, 200)
        size = 32 if size is None else size
        if icon.startswith('icon:'):
            # Встроенная SVG-иконка набора (принимает цвет элемента)
            svg = _svg_icon_html(icon[5:], size=max(12, min(200, int(size) * 2)))
            if not svg:
                return ''
            content = (f'<div style="width:100%;height:100%;display:flex;align-items:center;'
                       f'justify-content:center;color:{color};">{svg}</div>')
        elif len(icon) <= 4 and '<' not in icon and '>' not in icon:
            content = (f'<div style="width:100%;height:100%;display:flex;align-items:center;'
                       f'justify-content:center;color:{color};font-size:{size}px;line-height:1;">'
                       f'{esc(icon)}</div>')
        else:
            return ''

    link = _sanitize_url(el.get('link'))
    if link and etype != 'button':
        content = f'<a href="{esc(link)}" style="display:block;width:100%;height:100%;">{content}</a>'
    return f'<div data-el="{eid}" style="position:absolute;{";".join(styles)}">{content}</div>'


def _zero_responsive_css(block, uid):
    """Адаптив zero-блока: @media 1024px (tablet) и 640px (mobile)."""
    resp = block.get('responsive')
    if not isinstance(resp, dict):
        return ''
    axis = {'x': 'left', 'y': 'top', 'w': 'width', 'h': 'height'}
    chunks = []
    for media, maxw in (('tablet', 1024), ('mobile', 640)):
        rules = resp.get(media)
        if not isinstance(rules, dict):
            continue
        body = []
        for eid, cfg in rules.items():
            eid = str(eid)
            if not re.fullmatch(r'[a-z0-9\-]{1,40}', eid) or not isinstance(cfg, dict):
                continue
            props = []
            for k in ('x', 'y', 'w', 'h'):
                v = _pct(cfg.get(k))
                if v is not None:
                    props.append(f'{axis[k]}:{v}%')
            if cfg.get('hide') is True:
                props.append('display:none')
            if props:
                body.append(f'.cb-zero-{uid} [data-el="{eid}"]{{{";".join(props)}}}')
        if body:
            chunks.append(f'@media (max-width:{maxw}px){{' + ''.join(body) + '}')
    return ''.join(chunks)


def render_block(block):
    btype = block.get('type', 'text')
    if btype == 'heading':
        level = block.get('level', 'h2')
        if level not in ('h1', 'h2', 'h3'):
            level = 'h2'
        align = {'left': 'left', 'center': 'center', 'right': 'right'}.get(block.get('align'), 'left')
        typo = _typo(block, 'title')
        h_style = f' style="{typo}"' if typo else ''
        inner = f'<{level}{h_style}>{esc(block.get("text"))}</{level}>'
        return _block_wrapper(block, inner, extra_style=f'text-align:{align}')

    if btype == 'text':
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', block.get('text', '')) if p.strip()]
        if not paragraphs:
            return ''
        inner = ''.join(f'<p>{esc(p).replace(chr(10), "<br>")}</p>' for p in paragraphs)
        inner = _div(_typo(block, 'text'), inner)
        align = {'left': 'left', 'center': 'center', 'right': 'right'}.get(block.get('align'), 'left')
        return _block_wrapper(block, inner, extra_style=f'text-align:{align}')

    if btype == 'image':
        src = (block.get('src') or '').strip()
        if not src:
            # Заглушка noimage: без неё блок пропадает с холста редактора
            # (маркер не эмитится) и его невозможно выбрать, чтобы назначить картинку.
            alt0 = esc(block.get('alt', ''))
            inner0 = ('<figure style="margin:0;display:inline-block;max-width:100%;">'
                      '<img src="/static/noimage.png" alt="' + alt0 + '" loading="lazy" '
                      'style="width:100%;height:auto;border-radius:8px;opacity:.7;"></figure>')
            align0 = {'left': 'left', 'center': 'center', 'right': 'right'}.get(block.get('align'), 'center')
            return _block_wrapper(block, inner0, extra_style='margin:1.5rem 0;text-align:' + align0)
        alt = esc(block.get('alt', ''))
        caption = (block.get('caption') or '').strip()
        cap_typo = _typo(block, 'caption')
        cap_css = 'font-size:0.85rem;color:#888;text-align:center;margin-top:0.5rem;' + cap_typo
        cap_html = f'<figcaption style="{cap_css}">{esc(caption)}</figcaption>' if caption else ''
        align = {'left': 'left', 'center': 'center', 'right': 'right'}.get(block.get('align'), 'center')
        width = (str(block.get('width') or '').strip())
        width_css = f'width:{width};max-width:100%;' if re.fullmatch(r'\d{1,4}(\.\d+)?(px|%|rem|em)', width) else 'max-width:100%;'
        inner = (f'<figure style="margin:0;display:inline-block;{width_css}">'
                 f'<img src="{esc(src)}" alt="{alt}" loading="lazy" style="width:100%;height:auto;border-radius:8px;">'
                 f'{cap_html}</figure>')
        return _block_wrapper(block, inner, extra_style=f'margin:1.5rem 0;text-align:{align}')

    if btype == 'button':
        text = (block.get('text') or '').strip() or 'Подробнее'
        action = (block.get('action') or 'link').strip()
        if action not in ('link', 'anchor', 'tel', 'mailto', 'form', 'subscribe', 'popup'):
            action = 'link'
        align = block.get('align', 'left')
        align_css = {'left': 'left', 'center': 'center', 'right': 'right'}.get(align, 'left')
        bstyle, battrs = _button_style(block)
        if not bstyle:
            color = (block.get('color') or '#b49d84').strip() or '#b49d84'
            bstyle = (f'display:inline-block;background:{esc(color)};color:#fff;'
                      f'padding:0.8rem 2rem;border-radius:6px;text-decoration:none;font-weight:600;')

        href = '#'
        extra = ''
        if action == 'link':
            href = _sanitize_url(block.get('url')) or '#'
        elif action == 'anchor':
            anchor = re.sub(r'[^A-Za-z0-9_\-]', '', str(block.get('anchor') or ''))[:60]
            href = '#' + anchor if anchor else '#'
        elif action == 'tel':
            phone = re.sub(r'[^\d+]', '', str(block.get('phone') or ''))[:20]
            href = 'tel:' + phone if phone else '#'
        elif action == 'mailto':
            email = re.sub(r'[^A-Za-z0-9@._\-]', '', str(block.get('email') or ''))[:200]
            href = 'mailto:' + email if email else '#'
        elif action in ('form', 'subscribe'):
            # JS-обработчик (app.py, data-wb-ctor-action) открывает попап
            # с формой заявки / подписки. href="#" — фолбэк без JS.
            extra = f' data-wb-ctor-action="{action}"'
        elif action == 'popup':
            title = (block.get('popup_title') or '').strip()[:200]
            body = (block.get('popup_text') or '').strip()[:5000]
            # Текст попапа кладём в скрытый div рядом с кнопкой (не в
            # data-атрибут — там кавычки/разметка ломаются); JS показывает его.
            popup_html = ''
            if title or body:
                body_html = '<br>'.join(esc(body).split('\n'))
                popup_html = (f'<div class="wb-ctor-popup-content" hidden>'
                              f'<div class="wb-ctor-popup-title">{esc(title)}</div>'
                              f'<div class="wb-ctor-popup-body">{body_html}</div></div>')
            extra = ' data-wb-ctor-action="popup"'
            inner = (f'<a href="#" class="btn btn-default btn-lg"{battrs}{extra} style="{bstyle}">{esc(text)}</a>'
                     + popup_html)
            return _block_wrapper(block, inner, extra_style=f'text-align:{align_css};margin:1.5rem 0')

        inner = f'<a href="{esc(href)}" class="btn btn-default btn-lg"{battrs}{extra} style="{bstyle}">{esc(text)}</a>'
        return _block_wrapper(block, inner, extra_style=f'text-align:{align_css};margin:1.5rem 0')

    if btype == 'video':
        url = (block.get('url') or '').strip()
        yt = _youtube_id(url)
        vm = _vimeo_id(url)
        if yt:
            embed = f'https://www.youtube.com/embed/{yt}'
        elif vm:
            embed = f'https://player.vimeo.com/video/{vm}'
        else:
            # Пустая/неподдерживаемая ссылка — показываем заглушку, чтобы блок
            # был виден на холсте и на сайте (иначе блок «теряется» невидимым)
            inner = ('<div style="border:2px dashed #c9c9c9;border-radius:8px;'
                     'padding:2.5rem 1rem;text-align:center;color:#999;'
                     'font-size:0.95rem;background:#fafafa;">'
                     '🎬 Видео: укажите ссылку YouTube или Vimeo в настройках блока</div>')
            return _block_wrapper(block, inner, extra_style='margin:1.5rem 0')
        inner = (f'<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:8px;">'
                 f'<iframe src="{embed}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;"'
                 f' allowfullscreen loading="lazy"></iframe></div>')
        return _block_wrapper(block, inner, extra_style='margin:1.5rem 0')

    if btype == 'products':
        # Блок «Товары» (CN-2): режимы all/wb/ozon по источнику, фильтр по
        # категории, подборка «Рекомендации» (популярное). Рендер серверный.
        mode = block.get('mode', 'source')          # source | category | recommendations
        source = block.get('source', 'all')         # wb | ozon | all
        category = (block.get('category') or '').strip()
        limit = block.get('limit', 8)
        try:
            limit = max(1, min(48, int(limit)))
        except (TypeError, ValueError):
            limit = 8
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        title_html = f'<h2 style="font-size:1.6rem;margin-bottom:1.5rem;{title_typo}">{esc(title)}</h2>' if title else ''

        products = []
        try:
            if mode == 'recommendations':
                import recommendations
                products = recommendations.popular_products(
                    _load_products(), limit=limit)
            else:
                products = _load_products()
                if source in ('wb', 'ozon'):
                    products = [p for p in products if p.get('source') == source]
                if mode == 'category' and category:
                    products = [p for p in products if p.get('category') == category]
                products = products[:limit]
        except Exception:
            products = []

        cards = _product_cards_html(products)
        inner = (f'<div class="maxwidth-theme">{title_html}'
                 f'<div class="wb-products-grid" style="display:grid;'
                 f'grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:1.5rem;">'
                 f'{cards}</div></div>')
        return _block_wrapper(block, inner, extra_style='padding:2rem 0')

    if btype == 'divider':
        inner = '<hr style="border:0;border-top:1px solid #e0e0e0;margin:2rem 0;">'
        return _block_wrapper(block, inner)

    if btype == 'reviews':
        cards = []
        item_typo = _typo(block, 'item_text')
        for it in _parse_items(block.get('items')):
            text = str(it.get('text') or '').strip()
            if not text:
                continue
            text_html = f'<p style="{item_typo}">{esc(text)}</p>' if item_typo else f'<p>{esc(text)}</p>'
            cards.append(
                '<div class="cb-review-card">'
                f'<div class="cb-review-head"><span class="cb-review-name">{esc(it.get("name") or "Покупатель")}</span>'
                f'<span class="cb-review-stars">{_stars(it.get("rating", 5))}</span></div>'
                f'{text_html}</div>')
        if not cards:
            return ''
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        title_html = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        inner = f'<div class="maxwidth-theme">{title_html}' \
                f'<div class="cb-reviews-grid">{"".join(cards)}</div></div>'
        return _block_wrapper(block, inner)

    if btype == 'form':
        title = (block.get('title') or '').strip()
        button = (block.get('button') or '').strip() or 'Отправить'
        recipient = (block.get('recipient') or '').strip()
        note = f'Форма: {title}' if title else 'Форма обратной связи'
        if recipient:
            note += f' (для: {recipient})'
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        bstyle, battrs = _button_style(block)
        if bstyle:
            submit = f'<button type="submit"{battrs} style="{bstyle}">{esc(button)}</button>'
        else:
            submit = f'<button type="submit">{esc(button)}</button>'
        # Обычный POST без JS: /api/order сам перенаправит обратно на страницу
        inner = (f'<div class="maxwidth-theme">{heading}'
                 f'<form action="/api/order" method="post" class="cb-form-fields">'
                 f'<input type="hidden" name="redirect" value="1">'
                 f'<input type="hidden" name="product_name" value="{esc(note)}">'
                 '<input type="text" name="name" placeholder="Ваше имя" required>'
                 '<input type="tel" name="phone" placeholder="Телефон" required>'
                 '<input type="email" name="email" placeholder="Email (необязательно)">'
                 '<label style="display:flex;align-items:flex-start;gap:.4rem;font-size:.8rem;color:#666;cursor:pointer;">'
                 '<input type="checkbox" name="consent_pd" value="1" required style="margin-top:.15rem;">'
                 '<span>Соглашаюсь с обработкой персональных данных</span></label>'
                 f'{submit}'
                 '</form></div>')
        return _block_wrapper(block, inner)

    if btype == 'map':
        address = (block.get('address') or '').strip()
        if not address:
            return ''
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        zoom = str(block.get('zoom') or '16')
        zoom = zoom if re.fullmatch(r'\d{1,2}', zoom) and 9 <= int(zoom) <= 18 else '16'
        src = f'https://yandex.ru/map-widget/v1/?text={quote(address)}&z={zoom}'
        inner = (f'<div class="maxwidth-theme">{heading}'
                 f'<iframe src="{src}" loading="lazy" title="Карта"></iframe></div>')
        return _block_wrapper(block, inner)

    if btype == 'faq':
        items = []
        for it in _parse_items(block.get('items')):
            q = str(it.get('q') or '').strip()
            a = str(it.get('a') or '').strip()
            if not q or not a:
                continue
            items.append(f'<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>')
        if not items:
            # Заглушка: опорожнённый блок иначе исчезает с холста редактора
            return _block_wrapper(block, (
                '<div style="border:2px dashed #c9c9c9;border-radius:8px;'
                'padding:2rem 1rem;text-align:center;color:#999;'
                'font-size:0.95rem;background:#fafafa;">'
                '❓ FAQ: добавьте вопросы в настройках блока</div>'))
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        title_html = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        inner = f'<div class="maxwidth-theme">{title_html}{"".join(items)}</div>'
        return _block_wrapper(block, inner)

    if btype == 'banner':
        title = (block.get('title') or '').strip()
        if not title:
            return ''
        subtitle = (block.get('subtitle') or '').strip()
        button = (block.get('button') or '').strip()
        url = _sanitize_url(block.get('url')) or '#'
        bg = (block.get('bg') or '#b49d84').strip() or '#b49d84'
        if not re.fullmatch(r'#[0-9a-fA-F]{3,8}', bg):
            bg = '#b49d84'
        text_color = (block.get('text_color') or '#ffffff').strip()
        if not re.fullmatch(r'#[0-9a-fA-F]{3,8}', text_color):
            text_color = '#ffffff'
        image = (block.get('image') or '').strip()
        height = str(block.get('height') or '').strip()
        height_css = f'min-height:{int(height)}px;' if re.fullmatch(r'\d{2,3}', height) else ''
        if image:
            bg_css = (f'background:linear-gradient(rgba(0,0,0,0.25),rgba(0,0,0,0.25)),'
                      f'url("{esc(image)}") center/cover no-repeat {bg};')
        else:
            bg_css = f'background:{bg};'
        sub_html = f'<p class="cb-banner-sub" style="{_typo(block, "subtitle")}">{esc(subtitle)}</p>' if subtitle else ''
        if button:
            bstyle, battrs = _button_style(block)
            if bstyle:
                btn_html = f'<a class="cb-banner-btn"{battrs} href="{esc(url)}" style="{bstyle}">{esc(button)}</a>'
            else:
                btn_html = f'<a class="cb-banner-btn" href="{esc(url)}">{esc(button)}</a>'
        else:
            btn_html = ''
        title_typo = _typo(block, 'title')
        inner = (f'<div class="maxwidth-theme">'
                 f'<div class="cb-banner-inner" style="{bg_css}{height_css}color:{text_color};">'
                 f'<h2 class="cb-banner-title" style="color:{text_color};{title_typo}">{esc(title)}</h2>{sub_html}{btn_html}'
                 '</div></div>')
        # legacy=False: bg/padding/text_color баннера обрабатываются внутри
        return _block_wrapper(block, inner, legacy=False)

    if btype == 'html':
        # Блок «HTML-код» (CN-2): рендерится без эскейпинга ТОЛЬКО для
        # суперадмина; остальным — комментарий-заглушка (сохранение таких
        # блоков не-суперадмином отсекает ещё и сервер, app.py page-save).
        # Этап 6.1 (defense in depth): даже суперадминский HTML проходит
        # через санитайзер — script/iframe/on* не доходят до страницы.
        # Без обёртки: тест на точное равенство заглушки, raw HTML целиком.
        if _is_superadmin():
            raw_html = block.get('html', '') or ''
            if not raw_html.strip():
                # Пустой HTML — заглушка, иначе блок невидим на холсте
                return ('<div style="border:2px dashed #c9c9c9;border-radius:8px;'
                        'padding:2rem 1rem;text-align:center;color:#999;'
                        'font-size:0.95rem;background:#fafafa;">'
                        '⚙ HTML-блок: добавьте код в настройках блока</div>')
            return _sanitize_html(raw_html)
        return '<!-- html block: restricted -->'

    if btype == 'timer':
        # Блок «Таймер акции» (CN-2): обратный отсчёт до даты окончания.
        # Отсчёт работает на инлайн-JS-виджете TIMER_HYDRATOR (см. ниже).
        title = (block.get('title') or '').strip()
        date = (block.get('date') or '').strip()
        try:
            from datetime import datetime
            datetime.fromisoformat(date)
            valid = True
        except (ValueError, TypeError):
            valid = False
        button_text = (block.get('button_text') or '').strip()
        button_link = _sanitize_url(block.get('button_link')) or ''
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        if button_text:
            bstyle, battrs = _button_style(block)
            if bstyle:
                btn_html = f'<a class="cb-timer-btn"{battrs} href="{esc(button_link or "#")}" style="{bstyle}">{esc(button_text)}</a>'
            else:
                btn_html = f'<a class="cb-timer-btn" href="{esc(button_link or "#")}">{esc(button_text)}</a>'
        else:
            btn_html = ''
        date_attr = esc(date) if valid else ''
        inner = (f'<div class="maxwidth-theme">{heading}'
                 f'<div class="cb-timer-widget"><span class="cb-timer-countdown">…</span></div>'
                 f'{btn_html}</div>')
        return _block_wrapper(block, inner, extra_attrs=f' data-cb-timer-date="{date_attr}"')

    if btype == 'section':
        # Секция перенесённого дизайна: готовый HTML целиком (миграция со
        # скопированных страниц). Редактируется прямо на странице как единица.
        # При заданных фоне/отступах/цвете оборачивается в стилизуемый контейнер.
        html = block.get('html', '') or ''
        style = _style_attr(block)
        if style:
            return f'<div class="cb-section"{style}>{html}</div>'
        return html

    if btype == 'text_image':
        title = (block.get('title') or '').strip()
        text = (block.get('text') or '').strip()
        src = (block.get('src') or '').strip()
        image_pos = 'right' if block.get('image_pos') == 'right' else 'left'
        if not text and not src:
            return ''
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        paragraphs = ''.join(f'<p>{esc(p).replace(chr(10), "<br>")}</p>'
                             for p in re.split(r'\n\s*\n', text) if p.strip())
        paragraphs = _div(_typo(block, 'text'), paragraphs)
        img_html = (f'<img src="{esc(src)}" alt="{esc(block.get("alt") or title)}" loading="lazy" '
                    f'style="width:100%;height:auto;border-radius:10px;display:block;">') if src else ''
        col_img = f'<div class="cb-ti-img">{img_html}</div>' if img_html else ''
        col_text = f'<div class="cb-ti-text">{heading}{paragraphs}</div>'
        cols = col_img + col_text if image_pos == 'left' else col_text + col_img
        inner = f'<div class="maxwidth-theme"><div class="cb-ti-grid">{cols}</div></div>'
        return _block_wrapper(block, inner)

    if btype == 'gallery':
        imgs = []
        for it in _parse_items(block.get('items')):
            src = (it.get('src') or '').strip()
            if src:
                imgs.append(f'<div class="cb-gallery-item"><img src="{esc(src)}" alt="{esc(it.get("alt") or "")}" loading="lazy"></div>')
        if not imgs:
            # Заглушка noimage: пустая галерея иначе исчезает с холста редактора.
            # Заголовок выводим в обеих ветках — иначе title теряется у пустой галереи.
            title0 = (block.get('title') or '').strip()
            heading0 = f'<h2 style="{_typo(block, "title")}">{esc(title0)}</h2>' if title0 else ''
            inner = ('<div class="maxwidth-theme">' + heading0 + '<div class="cb-gallery-grid">'
                     '<div class="cb-gallery-item"><img src="/static/noimage.png" alt="" loading="lazy" style="opacity:.7"></div>'
                     '<div class="cb-gallery-item"><img src="/static/noimage.png" alt="" loading="lazy" style="opacity:.7"></div>'
                     '<div class="cb-gallery-item"><img src="/static/noimage.png" alt="" loading="lazy" style="opacity:.7"></div>'
                     '</div></div>')
            return _block_wrapper(block, inner)
        cols = str(block.get('cols', 3))
        cols = cols if cols in ('2', '3', '4') else '3'
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        inner = (f'<div class="maxwidth-theme">{heading}'
                 f'<div class="cb-gallery-grid">{"".join(imgs)}</div></div>')
        return _block_wrapper(block, inner)

    if btype == 'features':
        items = []
        t_title = _typo(block, 'item_title')
        t_text = _typo(block, 'item_text')
        for it in _parse_items(block.get('items')):
            title = str(it.get('title') or '').strip()
            text = str(it.get('text') or '').strip()
            if not title and not text:
                continue
            icon = (it.get('icon') or '').strip()
            icon_html = _icon_html(icon, size=40)
            items.append(
                '<div class="cb-feature-card">'
                f'{icon_html}<div class="cb-feature-title" style="{t_title}">{esc(title)}</div>'
                f'<div class="cb-feature-text" style="{t_text}">{esc(text)}</div></div>')
        if not items:
            # Заглушка: опорожнённый блок иначе исчезает с холста редактора
            return _block_wrapper(block, (
                '<div style="border:2px dashed #c9c9c9;border-radius:8px;'
                'padding:2rem 1rem;text-align:center;color:#999;'
                'font-size:0.95rem;background:#fafafa;">'
                '✨ Преимущества: добавьте пункты в настройках блока</div>'))
        cols = str(block.get('cols', 4))
        cols = cols if cols in ('2', '3', '4', '5') else '4'
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        inner = (f'<div class="maxwidth-theme">{heading}'
                 f'<div class="cb-features-grid">{"".join(items)}</div></div>')
        return _block_wrapper(block, inner)

    if btype == 'table':
        rows = []
        raw = _parse_items(block.get('items'))
        header_row = bool(block.get('header_row'))
        for ri, it in enumerate(raw):
            cells = it.get('cells')
            if isinstance(cells, str):
                cells = [c.strip() for c in cells.split('|')]
            cells = cells or []
            if not cells:
                continue
            if header_row and ri == 0:
                rows.append('<tr>' + ''.join(f'<th>{esc(c)}</th>' for c in cells) + '</tr>')
            else:
                rows.append('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in cells) + '</tr>')
        if not rows:
            return ''
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        inner = (f'<div class="maxwidth-theme">'
                 f'{heading}<div class="cb-table-wrap"><table>{"".join(rows)}</table></div></div>')
        return _block_wrapper(block, inner)

    if btype == 'spacer':
        h = str(block.get('height', '40')).strip()
        h = h if re.fullmatch(r'\d{1,3}', h) else '40'
        return _block_wrapper(block, '', extra_style=f'height:{h}px')

    if btype == 'text_columns':
        cols_raw = _parse_items(block.get('items'))
        t_text = _typo(block, 'text')
        cols = []
        for it in cols_raw:
            t = str(it.get('text') or '').strip()
            if not t:
                continue
            paragraphs = ''.join(f'<p>{esc(p).replace(chr(10), "<br>")}</p>'
                                 for p in re.split(r'\n\s*\n', t) if p.strip())
            cols.append(f'<div class="cb-col">{_div(t_text, paragraphs)}</div>')
        if not cols:
            return ''
        n = str(min(4, max(2, len(cols))))
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        inner = (f'<div class="maxwidth-theme">{heading}'
                 f'<div class="cb-cols-grid">{"".join(cols)}</div></div>')
        return _block_wrapper(block, inner)

    if btype == 'contacts':
        title = (block.get('title') or '').strip()
        title_typo = _typo(block, 'title')
        heading = f'<h2 style="{title_typo}">{esc(title)}</h2>' if title else ''
        phone = (block.get('phone') or '').strip()
        email = (block.get('email') or '').strip()
        address = (block.get('address') or '').strip()
        worktime = (block.get('worktime') or '').strip()
        rows = ''
        if phone:
            rows += f'<div class="cb-contact-row"><span>Телефон</span><a href="tel:{esc(re.sub(r"[^+\d]", "", phone))}">{esc(phone)}</a></div>'
        if email:
            rows += f'<div class="cb-contact-row"><span>Email</span><a href="mailto:{esc(email)}">{esc(email)}</a></div>'
        if address:
            rows += f'<div class="cb-contact-row"><span>Адрес</span><span>{esc(address)}</span></div>'
        if worktime:
            rows += f'<div class="cb-contact-row"><span>Режим работы</span><span>{esc(worktime)}</span></div>'
        inner = (f'<div class="maxwidth-theme">'
                 f'{heading}<div class="cb-contacts-card">{rows}</div></div>')
        return _block_wrapper(block, inner)

    if btype == 'zero':
        # Zero-блок (CN-T8): элементы в процентах контейнера + адаптив.
        height = _px(block.get('height'), 100, 2000)
        height = 500 if height is None else height
        elements = block.get('elements')
        if not isinstance(elements, list):
            elements = _parse_items(elements)
        uid = _block_uid('zero', json.dumps(elements, sort_keys=True, ensure_ascii=False, default=str))
        els_html = ''.join(_zero_element_html(el, i) for i, el in enumerate(elements))
        resp_css = _zero_responsive_css(block, uid)
        inner = (f'<div class="cb-zero cb-zero-{uid}" style="position:relative;'
                 f'height:{height}px;">{els_html}</div>')
        if resp_css:
            inner += f'<style>{resp_css}</style>'
        return _block_wrapper(block, inner, uid=uid)

    return ''


# JS-гидратор для блоков «Товары»: подгружает карточки с публичных API
# и инициализирует кнопки лайка/покупки через window.initWbActions.
# JS-виджет блока «Таймер акции» (CN-2): обратный отсчёт «ДД чч мм сс»
# до дедлайна; если дата прошла — «Акция завершена». Без внешних библиотек.
TIMER_HYDRATOR = """<script>
(function(){
    function pad(n){return (n<10?'0':'')+n;}
    function tick(el){
        var raw = el.getAttribute('data-cb-timer-date') || '';
        var target = null;
        try { target = new Date(raw); } catch (e) {}
        var out = el.querySelector('.cb-timer-countdown');
        if (!out) return;
        if (!target || isNaN(target.getTime())) { out.textContent = 'Укажите дату окончания акции'; return; }
        var diff = target.getTime() - Date.now();
        if (diff <= 0) { out.textContent = 'Акция завершена'; return; }
        var s = Math.floor(diff / 1000);
        var d = Math.floor(s / 86400); s -= d * 86400;
        var h = Math.floor(s / 3600); s -= h * 3600;
        var m = Math.floor(s / 60); s -= m * 60;
        out.textContent = pad(d) + ' дн ' + pad(h) + ' ч ' + pad(m) + ' мин ' + pad(s) + ' сек';
    }
    document.querySelectorAll('.cb-timer[data-cb-timer-date]').forEach(function(el){
        tick(el);
        setInterval(function(){ tick(el); }, 1000);
    });
})();
</script>"""


# Анимации появления и общие эффекты блоков (цикл 2, CN-T4): CSS-классы
# начального/конечного состояния + IntersectionObserver. Подключается в
# render_blocks только если какой-то блок реально использует эффекты.
ANIMATIONS_ASSETS = """<style>
.cb-anim-fade,.cb-anim-slide-up,.cb-anim-slide-left,.cb-anim-slide-right,.cb-anim-zoom{opacity:0;transition-property:opacity,transform;transition-timing-function:ease;}
.cb-anim-slide-up{transform:translateY(40px);}
.cb-anim-slide-left{transform:translateX(-40px);}
.cb-anim-slide-right{transform:translateX(40px);}
.cb-anim-zoom{transform:scale(0.92);}
.cb-anim-in{opacity:1;transform:none;}
.cb-sticky{position:sticky;top:0;z-index:50;}
.cb-hidden{display:none;}
.cb-bg-parallax{background-attachment:fixed;}
@media (max-width:640px){.cb-hide-mobile{display:none;}}
@media (min-width:641px){.cb-hide-desktop{display:none;}}
.cb-container-full .maxwidth-theme{max-width:100%;}
[data-cb-btn-hover="darken"]{transition:filter .2s ease;}
[data-cb-btn-hover="darken"]:hover{filter:brightness(0.85);}
[data-cb-btn-hover="lift"]{transition:transform .2s ease,box-shadow .2s ease;}
[data-cb-btn-hover="lift"]:hover{transform:translateY(-2px);}
</style>
<script>
(function(){
    var els = document.querySelectorAll('[data-cb-anim]');
    if (!els.length) return;
    function show(el){
        var d = parseInt(el.getAttribute('data-cb-anim-duration'), 10) || 600;
        var dl = parseInt(el.getAttribute('data-cb-anim-delay'), 10) || 0;
        el.style.transitionDuration = d + 'ms';
        el.style.transitionDelay = dl + 'ms';
        el.classList.add('cb-anim-in');
    }
    if (!('IntersectionObserver' in window)) {
        els.forEach(function(el){ show(el); });
        return;
    }
    var io = new IntersectionObserver(function(entries){
        entries.forEach(function(en){
            if (en.isIntersecting) { show(en.target); io.unobserve(en.target); }
        });
    }, {threshold: 0.12});
    els.forEach(function(el){ io.observe(el); });
})();
</script>"""


PRODUCTS_HYDRATOR = """<script>
(function(){
    function money(n){return (n||0).toLocaleString('ru-RU')+' ₽';}
    function cardHtml(p){
        var discount = (p.price&&p.final_price<p.price)?Math.round((p.price-p.final_price)/p.price*100):0;
        var priceHtml = discount
            ? '<span style="text-decoration:line-through;color:#999;font-size:0.9rem;">'+money(p.price)+'</span>'
              +'<span style="color:#c00;font-weight:700;font-size:1.15rem;margin-left:0.5rem;">'+money(p.final_price)+'</span>'
              +'<span style="background:#c00;color:#fff;font-size:0.75rem;padding:0.15rem 0.5rem;border-radius:4px;margin-left:0.5rem;">−'+discount+'%</span>'
            : '<span style="font-weight:700;font-size:1.15rem;">'+money(p.price)+'</span>';
        var adminDisc = p.admin_discount_percent>0?'<div style="margin-top:0.5rem;font-size:0.8rem;color:#137333;">Доп. скидка '+p.admin_discount_percent+'% уже применена</div>':'';
        var stock = (p.stock === undefined || p.stock === null) ? 10 : parseInt(p.stock, 10) || 0;
        var stockBadge = stock<=0
            ? '<div style="margin-top:0.5rem;font-size:0.82rem;font-weight:600;color:#c00;">Нет в наличии</div>'
            : (stock<=5
                ? '<div style="margin-top:0.5rem;font-size:0.82rem;font-weight:600;color:#b26a00;">Осталось мало</div>'
                : '<div style="margin-top:0.5rem;font-size:0.82rem;font-weight:600;color:#137333;">В наличии</div>');
        var buyDisabled = stock<=0;
        var photoSrc = (p.photo||'').charAt(0)==='/' ? p.photo : '/proxy/image?url='+encodeURIComponent(p.photo||'');
        return '<div class="wb-product-card" style="display:flex;flex-direction:column;border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;background:#fff;text-decoration:none;color:inherit;">'
          +'<div style="padding:1rem;background:#f9f9f9;"><img src="'+photoSrc+'" alt="" loading="lazy" style="width:100%;height:220px;object-fit:contain;display:block;"></div>'
          +'<div style="padding:1rem;display:flex;flex-direction:column;flex:1 0 auto;">'
          +'<div data-name="'+String(p.name||'').replace(/"/g,'&quot;')+'" style="font-weight:600;font-size:0.95rem;line-height:1.35;min-height:3.9em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;">'+(p.name||'')+'</div>'
          +'<div style="margin-top:0.75rem;display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;">'+priceHtml+'</div>'
          +adminDisc
          +stockBadge
          +'<div style="margin-top:0.75rem;font-size:0.85rem;color:#666;">Артикул: '+(p.id||'')+'</div>'
          +'<button type="button" class="wb-like-btn" data-id="'+p.id+'" data-name="'+String(p.name||'').replace(/"/g,'&quot;')+'" style="margin-top:auto;width:100%;background:#fff;color:#c00;border:1px solid #c00;padding:0.5rem;border-radius:6px;cursor:pointer;font-weight:600;"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" style="vertical-align:-2px;margin-right:2px;"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg> В избранное</button>'
          +'<button type="button" class="wb-buy-btn" data-id="'+p.id+'" data-name="'+String(p.name||'').replace(/"/g,'&quot;')+'" data-price="'+(p.final_price||p.price||0)+'" data-stock="'+stock+'"'+(buyDisabled?' disabled style="margin-top:0.5rem;width:100%;background:#ccc;color:#fff;border:0;padding:0.6rem;border-radius:6px;cursor:not-allowed;font-weight:600;"':' style="margin-top:0.5rem;width:100%;background:#b49d84;color:#fff;border:0;padding:0.6rem;border-radius:6px;cursor:pointer;font-weight:600;"')+'>Купить в 1 клик</button>'
          +'<form class="wb-order-form" data-id="'+p.id+'" style="display:none;margin-top:0.75rem;padding:0.75rem;background:#f9f9f9;border-radius:6px;">'
          +'<input type="text" name="name" placeholder="Ваше имя" required style="width:100%;margin-bottom:0.4rem;padding:0.4rem;border:1px solid #ddd;border-radius:4px;">'
          +'<input type="tel" name="phone" placeholder="Телефон" required style="width:100%;margin-bottom:0.4rem;padding:0.4rem;border:1px solid #ddd;border-radius:4px;">'
          +'<input type="email" name="email" placeholder="Email (необязательно)" style="width:100%;margin-bottom:0.4rem;padding:0.4rem;border:1px solid #ddd;border-radius:4px;">'
          +'<button type="submit" style="width:100%;background:#137333;color:#fff;border:0;padding:0.5rem;border-radius:4px;cursor:pointer;font-weight:600;">Оформить заявку</button>'
          +'<div class="wb-order-result" style="margin-top:0.4rem;font-size:0.8rem;"></div></form>'
          +'</div></div>';
    }
    async function hydrate(container){
        if (container.dataset.loaded) return;
        container.dataset.loaded = '1';
        var source = container.dataset.source || 'all';
        var limit = parseInt(container.dataset.limit || '8', 10);
        var products = [];
        try {
            if (source === 'wb' || source === 'all') {
                var r1 = await fetch('/api/wb-products').then(function(r){return r.json();});
                if (r1.success) products = products.concat(r1.products || []);
            }
        } catch (e) { /* оставляем пусто */ }
        products.slice(0, limit).forEach(function(p){ container.insertAdjacentHTML('beforeend', cardHtml(p)); });
        // Кнопки лайка/покупки обрабатывает общий скрипт сайта; подключаем его,
        // если страница ещё его не загружала (например, страница из конструктора).
        function bind(){ if (window.initWbActions) window.initWbActions(container); }
        if (window.initWbActions) {
            bind();
        } else {
            var s = document.createElement('script');
            s.src = '/static/js/wb-actions.js';
            s.onload = bind;
            document.head.appendChild(s);
        }
    }
    document.querySelectorAll('.wb-products-dynamic').forEach(hydrate);
})();
</script>"""


def _needs_assets(block):
    """Нужны ли странице анимационные ассеты (классы из ANIMATIONS_ASSETS)."""
    if not isinstance(block, dict):
        return False
    if block.get('hidden') is True:
        return True
    if block.get('container') == 'full' or block.get('bg_parallax') is True:
        return True
    fx = block.get('effects')
    if isinstance(fx, dict):
        if str(fx.get('anim') or 'none') in _ANIMS:
            return True
        if fx.get('sticky') is True or str(fx.get('hide_on') or 'none') in ('mobile', 'desktop'):
            return True
        if isinstance(fx.get('decor_top'), dict) or isinstance(fx.get('decor_bottom'), dict):
            return True
    cfg = block.get('button_style')
    if isinstance(cfg, dict) and str(cfg.get('hover') or 'none') in ('darken', 'lift'):
        return True
    if block.get('type') == 'zero':
        for el in _parse_items(block.get('elements')):
            props = el.get('props')
            if isinstance(props, dict):
                cfg = props.get('button_style')
                if isinstance(cfg, dict) and str(cfg.get('hover') or 'none') in ('darken', 'lift'):
                    return True
    return False


def _collect_google_fonts(blocks):
    """Уникальные font_google семейства всех блоков страницы (белый список)."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            fg = node.get('font_google')
            if isinstance(fg, str) and fg.strip() in _GOOGLE_FONTS_SET:
                found.add(fg.strip())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(blocks)
    return sorted(found)


def _page_extras(blocks):
    """Гидраторы, анимационные ассеты и Google Fonts для набора блоков."""
    out = ''
    if any(isinstance(b, dict) and b.get('type') == 'products' for b in blocks):
        out += '\n' + PRODUCTS_HYDRATOR
    if any(isinstance(b, dict) and b.get('type') == 'timer' for b in blocks):
        out += '\n' + TIMER_HYDRATOR
    if any(_needs_assets(b) for b in blocks):
        out += '\n' + ANIMATIONS_ASSETS
    families = _collect_google_fonts(blocks)
    if families:
        fam = '&family='.join(quote(f, safe='') for f in families)
        out += (f'\n<link rel="stylesheet" href="https://fonts.googleapis.com/'
                f'css2?family={fam}" data-cb-fonts="1">')
    return out


def render_blocks(blocks):
    """Превратить массив блоков в HTML для page.content_html."""
    if not isinstance(blocks, list):
        return ''
    parts = [render_block(b) for b in blocks if isinstance(b, dict)]
    html_out = '\n'.join(p for p in parts if p)
    return html_out + _page_extras(blocks)


def render_blocks_marked(blocks):
    """То же, но каждый блок обёрнут в HTML-комментарии-маркеры.

    Маркеры <!--blk:N--> ... <!--/blk--> не влияют на отображение, но позволяют
    редактору прямо на странице находить DOM-границы каждого блока.
    """
    if not isinstance(blocks, list):
        return ''
    parts = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        html = render_block(b)
        if html:
            parts.append(f'<!--blk:{i}-->\n{html}\n<!--/blk-->')
    out = '\n'.join(parts)
    return out + _page_extras(blocks)

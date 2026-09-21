#!/usr/bin/env python3
"""
Глобальные зоны шапка/подвал (CN-T6, серверная часть).

- default_zones(content)  — дефолтные конфиги из контента сайта
  (logo из site.logo, меню из content.menu — 2 уровня вложенности,
  соцсети из content.socials, телефон/емейл Экотории).
- get_zones()             — сохранённые site_settings.global_zones или дефолт.
- render_zone(kind, cfg, content) — безопасный HTML: всё экранируется,
  URL валидируются (http(s)/mailto/tel/относительные), меню — двухуровневый список.
- zones_css(cfg)          — CSS для sticky/transparent/height шапки.
- validate_zone(kind, cfg) — валидация структуры для POST /admin/api/global-zones
  (только известные ключи, проверка типов, строки ≤300, плохие URL отбрасываются).
"""
import re

import storage
from blocks import esc

DEFAULT_PHONE = '8 800 201 08 24'
DEFAULT_EMAIL = 'info@ekotoriya.ru'

HEADER_DEFAULTS = {
    'logo_image': '',            # URL картинки логотипа (или '')
    'logo_text': '',             # текстовый логотип, если картинки нет
    'menu': [],                  # [{title, url, children: [{title, url}]}]
    'phone': DEFAULT_PHONE,
    'email': DEFAULT_EMAIL,
    'cta': {'text': '', 'url': ''},
    'socials': [],               # [{icon, url, title?}]
    'sticky': False,
    'transparent': False,
    'height': 80,                # px
    'use_global': False,         # выключено по умолчанию (CN-T6)
}

FOOTER_DEFAULTS = {
    'columns': [],               # [{title, links: [{title, url}]}]
    'phone': DEFAULT_PHONE,
    'email': DEFAULT_EMAIL,
    'address': '',
    'socials': [],
    'copyright': '',
    'badges': [],                # [URL картинок]
    'use_global': False,
}

_URL_SCHEME_OK = ('http://', 'https://', 'mailto:', 'tel:', '/', '#')
_HEX_COLOR = re.compile(r'#[0-9a-fA-F]{3,8}')


def _safe_url(value):
    """Разрешить http(s)/mailto/tel/относительные ссылки; иначе пустая строка."""
    value = str(value or '').strip()
    if not value:
        return ''
    low = value.lower()
    if low.startswith(('javascript:', 'data:', 'vbscript:')):
        return ''
    return value if low.startswith(_URL_SCHEME_OK) else ''


def _safe_color(value, default=''):
    value = str(value or '').strip()
    return value if _HEX_COLOR.fullmatch(value) else default


def _s(value, max_len=300):
    """Строка, обрезанная до max_len символов."""
    return str(value or '').strip()[:max_len]


def _menu_from_content(menu):
    """content.menu → [{title, url, children:[{title,url}]}] (максимум 2 уровня)."""
    out = []
    for item in menu or []:
        if not isinstance(item, dict):
            continue
        children = []
        for child in (item.get('children') or []):
            if not isinstance(child, dict):
                continue
            children.append({'title': _s(child.get('title')), 'url': _safe_url(child.get('url'))})
        out.append({
            'title': _s(item.get('title')),
            'url': _safe_url(item.get('url')),
            'children': children,
        })
    return out


def _socials_from_content(socials):
    """content.socials → [{icon, url, title}]."""
    out = []
    for s in socials or []:
        if not isinstance(s, dict):
            continue
        out.append({
            'icon': _s(s.get('name') or s.get('icon')),
            'url': _safe_url(s.get('url')),
            'title': _s(s.get('title')),
        })
    return out


def default_zones(content):
    """Дефолтные конфиги зон из контента сайта (use_global=False, но конфиг заполнен)."""
    content = content or {}
    site = content.get('site', {}) or {}
    menu = _menu_from_content(content.get('menu'))
    socials = _socials_from_content(content.get('socials'))
    header = dict(HEADER_DEFAULTS)
    header['logo_image'] = _safe_url(site.get('logo'))
    header['logo_text'] = _s(site.get('name') or site.get('title') or 'Экотория')
    header['menu'] = menu
    header['socials'] = socials
    footer = dict(FOOTER_DEFAULTS)
    footer['columns'] = [{'title': 'Меню', 'links': [
        {'title': m['title'], 'url': m['url']} for m in menu]}] if menu else []
    footer['socials'] = socials
    footer['copyright'] = _s(site.get('name') or site.get('title') or 'Экотория')
    return {'header': header, 'footer': footer}


def get_zones(content=None):
    """Сохранённые global_zones или дефолт (без сохранения при первом обращении)."""
    stored = storage.get_setting('global_zones')
    if isinstance(stored, dict) and isinstance(stored.get('header'), dict) \
            and isinstance(stored.get('footer'), dict):
        return {
            'header': {**HEADER_DEFAULTS, **stored['header']},
            'footer': {**FOOTER_DEFAULTS, **stored['footer']},
        }
    if content is None:
        content = storage.load_content()
    return default_zones(content)


def _validate_links(items):
    """[{title, url}] с валидацией URL (плохой URL отбрасывается)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({'title': _s(it.get('title')), 'url': _safe_url(it.get('url'))})
    return out


def validate_zone(kind, cfg):
    """Очистить конфиг зоны: только известные ключи, типы, длины ≤300, валидные URL."""
    defaults = HEADER_DEFAULTS if kind == 'header' else FOOTER_DEFAULTS
    if not isinstance(cfg, dict):
        return dict(defaults)
    clean = {}
    for key, default in defaults.items():
        if key not in cfg:
            continue
        v = cfg[key]
        if isinstance(default, bool):
            clean[key] = bool(v)
        elif isinstance(default, int):
            try:
                clean[key] = max(30, min(300, int(v)))
            except (TypeError, ValueError):
                clean[key] = default
        elif isinstance(default, str):
            clean[key] = _s(v)
        elif key == 'menu' and isinstance(v, list):
            menu = []
            for it in v:
                if not isinstance(it, dict):
                    continue
                children = _validate_links(it.get('children'))
                menu.append({'title': _s(it.get('title')),
                             'url': _safe_url(it.get('url')),
                             'children': children})
            clean[key] = menu
        elif key == 'socials' and isinstance(v, list):
            socials = []
            for it in v:
                if not isinstance(it, dict):
                    continue
                socials.append({'icon': _s(it.get('icon')), 'url': _safe_url(it.get('url')),
                                'title': _s(it.get('title'))})
            clean[key] = socials
        elif key == 'cta' and isinstance(v, dict):
            clean[key] = {'text': _s(v.get('text')), 'url': _safe_url(v.get('url'))}
        elif key == 'columns' and isinstance(v, list):
            columns = []
            for it in v:
                if not isinstance(it, dict):
                    continue
                columns.append({'title': _s(it.get('title')),
                                'links': _validate_links(it.get('links'))})
            clean[key] = columns
        elif key == 'badges' and isinstance(v, list):
            clean[key] = [_safe_url(u) for u in v if _safe_url(u)]
    merged = dict(defaults)
    merged.update(clean)
    # URL-поля верхнего уровня: плохие значения отбрасываем
    for ukey in ('logo_image',):
        if ukey in merged:
            merged[ukey] = _safe_url(merged[ukey])
    # цвета, если переданы
    for ckey in ('bg_color', 'text_color'):
        if ckey in cfg:
            merged[ckey] = _safe_color(cfg.get(ckey))
    return merged


def _menu_html(menu):
    """Двухуровневый список ссылок (всё экранировано)."""
    items = []
    for it in menu or []:
        title = esc(it.get('title'))
        url = _safe_url(it.get('url')) or '#'
        children = it.get('children') or []
        sub = ''
        if children:
            sub = '<ul class="zone-menu-children">' + ''.join(
                f'<li><a href="{_safe_url(c.get("url")) or "#"}">{esc(c.get("title"))}</a></li>'
                for c in children) + '</ul>'
        items.append(f'<li class="zone-menu-item"><a href="{url}">{title}</a>{sub}</li>')
    if not items:
        return ''
    return '<ul class="zone-menu">' + ''.join(items) + '</ul>'


def _socials_html(socials):
    links = []
    for s in socials or []:
        url = _safe_url(s.get('url'))
        if not url:
            continue
        label = esc(s.get('title') or s.get('icon') or url)
        links.append(f'<a class="zone-social" href="{url}" target="_blank" rel="nofollow">{label}</a>')
    return '<div class="zone-socials">' + ''.join(links) + '</div>' if links else ''


def render_zone(kind, config, content=None):
    """Безопасный HTML зоны; '' если конфиг пустой/выключен (use_global=False)."""
    if not isinstance(config, dict):
        return ''
    if not config.get('use_global'):
        return ''
    if kind == 'header':
        try:
            height = max(30, min(300, int(config.get('height') or 80)))
        except (TypeError, ValueError):
            height = 80
        logo = ''
        logo_image = _safe_url(config.get('logo_image'))
        logo_text = esc(config.get('logo_text'))
        if logo_image:
            logo = (f'<a class="zone-logo" href="/"><img src="{logo_image}" alt="{logo_text}" '
                    f'style="max-height:{height}px;"></a>')
        elif logo_text:
            logo = f'<a class="zone-logo" href="/">{logo_text}</a>'
        phone = _s(config.get('phone'))
        phone_html = (f'<a class="zone-phone" href="tel:{esc(re.sub(r"[^+\d]", "", phone))}">{esc(phone)}</a>'
                      if phone else '')
        email = _s(config.get('email'))
        email_html = f'<a class="zone-email" href="mailto:{esc(email)}">{esc(email)}</a>' if email else ''
        cta = config.get('cta') or {}
        cta_html = ''
        cta_url = _safe_url(cta.get('url'))
        if cta.get('text') and cta_url:
            cta_html = f'<a class="zone-cta" href="{cta_url}">{esc(cta.get("text"))}</a>'
        classes = 'zone-header'
        if config.get('sticky'):
            classes += ' zone-sticky'
        if config.get('transparent'):
            classes += ' zone-transparent'
        return (f'<header class="{classes}" style="min-height:{height}px">'
                f'<div class="zone-inner maxwidth-theme">'
                f'{logo}{_menu_html(config.get("menu"))}'
                f'<div class="zone-contacts">{phone_html}{email_html}{cta_html}</div>'
                f'{_socials_html(config.get("socials"))}'
                f'</div></header>')
    if kind == 'footer':
        columns = config.get('columns') or []
        cols_html = ''
        for col in columns:
            links = ''.join(
                f'<li><a href="{_safe_url(l.get("url")) or "#"}">{esc(l.get("title"))}</a></li>'
                for l in (col.get('links') or []))
            cols_html += (f'<div class="zone-footer-col"><div class="zone-footer-title">'
                          f'{esc(col.get("title"))}</div><ul>{links}</ul></div>')
        phone = _s(config.get('phone'))
        contact = ''
        if phone:
            contact += f'<div><a href="tel:{esc(re.sub(r"[^+\d]", "", phone))}">{esc(phone)}</a></div>'
        email = _s(config.get('email'))
        if email:
            contact += f'<div><a href="mailto:{esc(email)}">{esc(email)}</a></div>'
        if config.get('address'):
            contact += f'<div>{esc(config.get("address"))}</div>'
        badges = ''.join(
            f'<img class="zone-badge" src="{_safe_url(u)}" alt="" loading="lazy">'
            for u in (config.get('badges') or []) if _safe_url(u))
        copyright_html = f'<div class="zone-copyright">{esc(config.get("copyright"))}</div>' \
            if config.get('copyright') else ''
        return (f'<footer class="zone-footer"><div class="zone-inner maxwidth-theme">'
                f'<div class="zone-footer-cols">{cols_html}</div>'
                f'<div class="zone-footer-contacts">{contact}{_socials_html(config.get("socials"))}</div>'
                f'{badges}{copyright_html}'
                f'</div></footer>')
    return ''


def zones_css(config):
    """CSS зон: высота/липкость/прозрачность шапки (для <style> в базовом шаблоне)."""
    if not isinstance(config, dict):
        return ''
    try:
        height = max(30, min(300, int(config.get('height') or 80)))
    except (TypeError, ValueError):
        height = 80
    css = [
        '.zone-header{background:var(--theme-bg, #fff);border-bottom:1px solid #eee;}'
        '.zone-inner{max-width:var(--theme-container-width, 1400px);margin:0 auto;'
        'padding:0 1rem;display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;}'
        '.zone-logo{font-weight:700;font-size:1.3rem;text-decoration:none;}'
        '.zone-menu{display:flex;gap:1.2rem;list-style:none;margin:0;padding:0;flex-wrap:wrap;}'
        '.zone-menu-item{position:relative;padding:0.8rem 0;}'
        '.zone-menu-item>a{text-decoration:none;}'
        '.zone-menu-children{position:absolute;top:100%;left:0;min-width:200px;background:#fff;'
        'border:1px solid #eee;border-radius:8px;list-style:none;margin:0;padding:0.5rem;display:none;z-index:50;}'
        '.zone-menu-item:hover .zone-menu-children{display:block;}'
        '.zone-menu-children li a{display:block;padding:0.4rem 0.6rem;text-decoration:none;}'
        '.zone-contacts{margin-left:auto;display:flex;gap:1rem;align-items:center;flex-wrap:wrap;}'
        '.zone-cta{background:var(--theme-primary, #b49d84);color:#fff;padding:0.5rem 1.2rem;'
        'border-radius:6px;text-decoration:none;font-weight:600;}'
        '.zone-socials{display:flex;gap:0.8rem;}'
        f'.zone-header{{min-height:{height}px;}}'
        '.zone-sticky{position:sticky;top:0;z-index:100;}'
        '.zone-transparent{background:transparent;border-bottom:0;}'
        '.zone-footer{background:#f7f7f7;margin-top:3rem;padding:2.5rem 0;}'
        '.zone-footer-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1.5rem;}'
        '.zone-footer-title{font-weight:600;margin-bottom:0.6rem;}'
        '.zone-footer ul{list-style:none;margin:0;padding:0;}'
        '.zone-footer ul li a{text-decoration:none;line-height:1.9;}'
        '.zone-footer-contacts{margin:1.5rem 0 0.5rem;}'
        '.zone-badge{height:32px;margin-right:0.6rem;}'
        '.zone-copyright{color:#888;font-size:0.85rem;margin-top:1rem;}'
    ]
    return '\n'.join(css)

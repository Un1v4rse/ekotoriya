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
    divider   — разделитель
    html      — произвольный HTML (для перенесённого дизайна, редактирование по желанию)

Страница с ключом 'blocks' рендерится из блоков; content_html генерируется
автоматически при сохранении в админке (app.py /admin/api/save).
"""
import html
import re
from urllib.parse import urlparse, parse_qs


def esc(value):
    return html.escape(str(value or ''), quote=True)


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


def render_block(block):
    btype = block.get('type', 'text')
    if btype == 'heading':
        level = block.get('level', 'h2')
        if level not in ('h1', 'h2', 'h3'):
            level = 'h2'
        return (f'<div class="cb cb-heading"><{level}>'
                f'{esc(block.get("text"))}</{level}></div>')

    if btype == 'text':
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', block.get('text', '')) if p.strip()]
        if not paragraphs:
            return ''
        inner = ''.join(f'<p>{esc(p).replace(chr(10), "<br>")}</p>' for p in paragraphs)
        return f'<div class="cb cb-text">{inner}</div>'

    if btype == 'image':
        src = (block.get('src') or '').strip()
        if not src:
            return ''
        alt = esc(block.get('alt', ''))
        caption = (block.get('caption') or '').strip()
        cap_html = f'<figcaption style="font-size:0.85rem;color:#888;text-align:center;margin-top:0.5rem;">{esc(caption)}</figcaption>' if caption else ''
        return (f'<div class="cb cb-image" style="margin:1.5rem 0;text-align:center;">'
                f'<figure style="margin:0;display:inline-block;max-width:100%;">'
                f'<img src="{esc(src)}" alt="{alt}" loading="lazy" style="max-width:100%;height:auto;border-radius:8px;">'
                f'{cap_html}</figure></div>')

    if btype == 'button':
        text = (block.get('text') or '').strip() or 'Подробнее'
        url = (block.get('url') or '#').strip() or '#'
        color = (block.get('color') or '#b49d84').strip() or '#b49d84'
        align = block.get('align', 'left')
        align_css = {'left': 'left', 'center': 'center', 'right': 'right'}.get(align, 'left')
        return (f'<div class="cb cb-button" style="text-align:{align_css};margin:1.5rem 0;">'
                f'<a href="{esc(url)}" class="btn btn-default btn-lg"'
                f' style="display:inline-block;background:{esc(color)};color:#fff;'
                f'padding:0.8rem 2rem;border-radius:6px;text-decoration:none;font-weight:600;">'
                f'{esc(text)}</a></div>')

    if btype == 'video':
        url = (block.get('url') or '').strip()
        yt = _youtube_id(url)
        vm = _vimeo_id(url)
        if yt:
            embed = f'https://www.youtube.com/embed/{yt}'
        elif vm:
            embed = f'https://player.vimeo.com/video/{vm}'
        else:
            return ''
        return (f'<div class="cb cb-video" style="margin:1.5rem 0;">'
                f'<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:8px;">'
                f'<iframe src="{embed}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;"'
                f' allowfullscreen loading="lazy"></iframe></div></div>')

    if btype == 'products':
        source = block.get('source', 'all')
        if source not in ('wb', 'all'):
            source = 'all'
        limit = block.get('limit', 8)
        try:
            limit = max(1, min(48, int(limit)))
        except (TypeError, ValueError):
            limit = 8
        title = (block.get('title') or '').strip()
        title_html = f'<h2 style="font-size:1.6rem;margin-bottom:1.5rem;">{esc(title)}</h2>' if title else ''
        return (f'<div class="cb cb-products" style="padding:2rem 0;">'
                f'<div class="maxwidth-theme">{title_html}'
                f'<div class="wb-products-dynamic" data-source="{source}" data-limit="{limit}"'
                f' style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:1.5rem;"></div>'
                f'</div></div>')

    if btype == 'divider':
        return '<div class="cb cb-divider"><hr style="border:0;border-top:1px solid #e0e0e0;margin:2rem 0;"></div>'

    if btype == 'html':
        return block.get('html', '') or ''

    return ''


# JS-гидратор для блоков «Товары»: подгружает карточки с публичных API
# и инициализирует кнопки лайка/покупки через window.initWbActions.
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
        return '<div class="wb-product-card" style="display:block;border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;background:#fff;text-decoration:none;color:inherit;">'
          +'<div style="padding:1rem;background:#f9f9f9;"><img src="/proxy/image?url='+encodeURIComponent(p.photo||'')+'" alt="" loading="lazy" style="width:100%;height:220px;object-fit:contain;display:block;"></div>'
          +'<div style="padding:1rem;">'
          +'<div data-name="'+String(p.name||'').replace(/"/g,'&quot;')+'" style="font-weight:600;font-size:0.95rem;line-height:1.35;min-height:3.9em;overflow:hidden;">'+(p.name||'')+'</div>'
          +'<div style="margin-top:0.75rem;display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;">'+priceHtml+'</div>'
          +adminDisc
          +'<div style="margin-top:0.75rem;font-size:0.85rem;color:#666;">Артикул: '+(p.id||'')+'</div>'
          +'<button type="button" class="wb-like-btn" data-id="'+p.id+'" data-name="'+String(p.name||'').replace(/"/g,'&quot;')+'" style="margin-top:0.75rem;width:100%;background:#fff;color:#c00;border:1px solid #c00;padding:0.5rem;border-radius:6px;cursor:pointer;font-weight:600;">❤ В избранное</button>'
          +'<button type="button" class="wb-buy-btn" data-id="'+p.id+'" data-name="'+String(p.name||'').replace(/"/g,'&quot;')+'" data-price="'+(p.final_price||p.price||0)+'" style="margin-top:0.5rem;width:100%;background:#b49d84;color:#fff;border:0;padding:0.6rem;border-radius:6px;cursor:pointer;font-weight:600;">Купить в 1 клик</button>'
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


def render_blocks(blocks):
    """Превратить массив блоков в HTML для page.content_html."""
    if not isinstance(blocks, list):
        return ''
    parts = [render_block(b) for b in blocks if isinstance(b, dict)]
    html_out = '\n'.join(p for p in parts if p)
    if any(isinstance(b, dict) and b.get('type') == 'products' for b in blocks):
        html_out += '\n' + PRODUCTS_HYDRATOR
    return html_out


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
    if any(isinstance(b, dict) and b.get('type') == 'products' for b in blocks):
        out += '\n' + PRODUCTS_HYDRATOR
    return out

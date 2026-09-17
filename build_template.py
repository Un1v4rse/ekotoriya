#!/usr/bin/env python3
"""
One-time script that converts all downloaded pages in static_original/pages/
into Jinja2 templates and extracts editable content into data/content.json.
"""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES_SRC = ROOT / 'static_original' / 'pages'
STATIC_ORIG = ROOT / 'static_original'
STATIC = ROOT / 'static'
TEMPLATES = ROOT / 'templates'
DATA = ROOT / 'data'

PAGES_TEMPLATES = TEMPLATES / 'pages'

STATIC.mkdir(exist_ok=True)
PAGES_TEMPLATES.mkdir(parents=True, exist_ok=True)
DATA.mkdir(exist_ok=True)

# Copy static assets from static_original, preserving files downloaded by download_assets.py
for item in STATIC_ORIG.iterdir():
    if item.name == 'index.html':
        continue
    if item.name == 'pages':
        # pages are processed as templates, not copied as static
        continue
    dest = STATIC / item.name
    if item.is_dir():
        shutil.copytree(item, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(item, dest)
print('static assets copied')


def rewrite_url(m):
    prefix = m.group(1)
    url = m.group(2)
    if url.startswith('data:') or url.startswith('http') or url.startswith('//') or url.startswith('#') or url.startswith('mailto:') or url.startswith('tel:'):
        return m.group(0)
    if url.startswith('/static/'):
        return m.group(0)
    # Rewrite local absolute paths to /static/... and drop cache-busting query strings.
    if url.startswith('/'):
        # Keep fragment (after #) for client-side uses like SVG sprites, but strip ?query.
        if '?' in url:
            base, rest = url.split('?', 1)
            fragment = ''
            if '#' in rest:
                fragment = '#' + rest.split('#', 1)[1]
            url = base + fragment
        return f'{prefix}/static{url}'
    return m.group(0)


def rewrite_html_urls(html):
    html = re.sub(r'(["\'])(/bitrix/[^"\']+)', rewrite_url, html)
    html = re.sub(r'(["\'])(/upload/[^"\']+)', rewrite_url, html)
    html = re.sub(r'(["\'])(/include/[^"\']+)', rewrite_url, html)
    html = re.sub(r'(["\'])(/favicon\.ico[^"\']*)', rewrite_url, html)
    return html


def find_matching_close(html, start_tag_end):
    depth = 1
    i = start_tag_end
    while i < len(html) and depth > 0:
        open_pos = html.find('<div', i)
        close_pos = html.find('</div>', i)
        if close_pos == -1:
            return -1
        if open_pos != -1 and open_pos < close_pos:
            depth += 1
            i = open_pos + 4
        else:
            depth -= 1
            i = close_pos + 6
            if depth == 0:
                return i
    return -1


def extract_content_block(html):
    """Extract the main content block (<div id=\"content\">)."""
    match = re.search(r'(<div[^>]*\bid=["\']content["\'][^>]*>)', html, re.I)
    if not match:
        return None, None, None
    start = match.start()
    tag_end = match.end()
    end = find_matching_close(html, tag_end)
    if end == -1:
        return None, None, None
    return html[start:tag_end], html[tag_end:end-6], html[end-6:end]


def replace_dynamic_regions(html):
    """Заменить статичные меню и соцсети на динамические Jinja-include,
    чтобы админ мог редактировать их в админке без кода."""
    # Верхнее mega-меню
    m = re.search(r'<nav[^>]*class="[^"]*mega-menu[^"]*"[^>]*>', html, re.I)
    if m:
        end = html.find('</nav>', m.end())
        if end != -1:
            html = html[:m.start()] + m.group(0) + "\n{% include 'site_menu.html' %}\n" + html[end:]
    # Мобильное бургер-меню
    m = re.search(r'<div[^>]*class="[^"]*burger_menu_wrapper[^"]*"[^>]*>', html, re.I)
    if m:
        end = find_matching_close(html, m.end())
        if end != -1:
            html = (html[:m.start()] + m.group(0) + "\n{% include 'site_burger_menu.html' %}\n</div>"
                    + html[end:])
    # Иконки соцсетей (все вхождения)
    html = re.sub(
        r'<!-- noindex -->\s*<ul>.*?</ul>\s*<!-- /noindex -->',
        "{% include 'site_socials.html' %}",
        html, flags=re.I | re.S
    )
    return html


def build_page_template(slug, src_path):
    html = src_path.read_bytes().decode('utf-8', errors='ignore')

    # Меню и соцсети — динамические, управляются из админки
    html = replace_dynamic_regions(html)

    # Extract metadata
    title_match = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
    title = re.sub(r'\s+', ' ', title_match.group(1)).strip() if title_match else ''

    h1_match = re.search(r'<h1[^>]*id=["\']pagetitle["\'][^>]*>(.*?)</h1>', html, re.I | re.S)
    h1 = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip() if h1_match else ''

    meta_desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)', html, re.I)
    meta_description = meta_desc_match.group(1) if meta_desc_match else ''

    # Extract content block
    content_html = ''
    hero_img = ''
    extracted = extract_content_block(html)
    if extracted[0] is not None:
        opening_tag, inner_html, closing_tag = extracted
        content_html = inner_html.strip()
        # Rewrite URLs inside content so rendered pages use local static assets
        content_html = rewrite_html_urls(content_html)
        # Find the first main-slider background image for LCP preload (homepage only)
        hero_img = ''
        if slug == 'index':
            m = re.search(r'data-bg="(/static/upload/iblock/[^"]+\.(?:jpg|jpeg|png|webp|gif))"', content_html)
            if not m:
                m = re.search(r'data-src="(/static/upload/iblock/[^"]+\.(?:jpg|jpeg|png|webp|gif))"', content_html)
            if m:
                hero_img = m.group(1)
                # Replace the lazy loader background on the first slide with the real image
                # so the Largest Contentful Paint element paints immediately.
                loader = '/static/bitrix/templates/aspro_max/images/loaders/double_ring.svg'
                content_html = content_html.replace(
                    f"url('{loader}')",
                    f"url('{hero_img}')",
                    1
                )
        # Replace content block in template with Jinja2 variable
        full_block = opening_tag + inner_html + closing_tag
        placeholder = opening_tag + '\n{{ page.content_html | safe }}\n' + closing_tag
        html = html.replace(full_block, placeholder, 1)
    else:
        # Fallback: whole body content editable? Keep full page as template
        # and content_html empty initially.
        pass

    # Rewrite URLs
    html = rewrite_html_urls(html)

    # Remove Bitrix BX.message inline scripts that contain raw newlines and break JS parsing.
    html = re.sub(r'<script>\s*BX\.message\(\{.*?\}\)\s*</script>\s*', '', html, flags=re.DOTALL)

    # Replace editable metadata with Jinja2 variables
    html = re.sub(r'<title>[^<]*</title>', f'<title>{{{{ page.title }}}}</title>', html, count=1, flags=re.I)
    if h1:
        html = re.sub(
            r'(<h1[^>]*id=["\']pagetitle["\'][^>]*>)[^<]*',
            r'\1{{ page.h1 }}',
            html,
            count=1,
            flags=re.I | re.S
        )
    if meta_description:
        html = re.sub(
            r'(<meta[^>]*name=["\']description["\'][^>]*content=["\'])[^"\']*',
            r'\1{{ page.meta_description }}',
            html,
            count=1,
            flags=re.I
        )

    # Replace page-specific og:title / og:url / og:description with variables
    html = re.sub(
        r'(<meta[^>]*property=["\']og:title["\'][^>]*content=["\'])[^"\']*',
        r'\1{{ page.title }}',
        html,
        count=1,
        flags=re.I
    )
    html = re.sub(
        r'(<meta[^>]*property=["\']og:url["\'][^>]*content=["\'])[^"\']*',
        r'\1{{ page.url }}',
        html,
        count=1,
        flags=re.I
    )

    # Insert product catalog blocks and recommendations.
    blocks = ''
    if slug == 'index':
        blocks += "{% include 'wb_catalog_block.html' %}\n{% include 'recommendations_block.html' %}\n"
    if slug == 'catalog__namatrasniki':
        blocks += "{% include 'wb_catalog_block.html' %}\n{% include 'recommendations_block.html' %}\n"
    if slug == 'catalog':
        blocks += "{% include 'wb_catalog_block.html' %}\n"
    if blocks and '</body>' in html:
        html = html.replace('</body>', blocks + '<script src=\"/static/js/wb-actions.js\"></script>\n</body>', 1)

    # Escape accidental Jinja2 syntax in the original HTML (e.g. Bitrix templates use {{ }}).
    # First protect our own placeholders, then escape remaining delimiters.
    markers = [
        ('{{ page.title }}', '__JINJA_TITLE__'),
        ('{{ page.h1 }}', '__JINJA_H1__'),
        ('{{ page.meta_description }}', '__JINJA_META__'),
        ('{{ page.url }}', '__JINJA_URL__'),
        ('{{ page.content_html | safe }}', '__JINJA_CONTENT__'),
    ]
    for placeholder, marker in markers:
        html = html.replace(placeholder, marker)
    html = html.replace('{{', "{{ '{{' }}")
    html = html.replace('}}', "{{ '}}' }}")
    for placeholder, marker in markers:
        html = html.replace(marker, placeholder)

    # Insert dynamic theme stylesheet before </head> so site colors/fonts can be edited from admin.
    # Done after escaping so our own Jinja2 expression stays intact.
    if '</head>' in html:
        head_extras = '<link rel="stylesheet" href="/theme.css?v={{ site.theme.version | default(1) }}">\n'
        html = html.replace('</head>', head_extras + '</head>', 1)

    # Preload the first main slider background image on the homepage to improve LCP.
    # Insert it right after the Content-Type meta so it is discovered early.
    if slug == 'index' and hero_img:
        preload = f'<link rel="preload" fetchpriority="high" as="image" href="{hero_img}">\n'
        preload += '<style>\n  /* До инициализации swiper первый слайд должен быть виден (защита от FOUC), но высоту не фиксируем — её задаёт swiper. */\n  .main-slider__wrapper > .swiper-slide:first-child { width: 100% !important; opacity: 1 !important; visibility: visible !important; background-size: cover !important; background-position: center !important; }\n</style>\n'
        html = re.sub(
            r'(<meta[^>]*http-equiv=["\']Content-Type["\'][^>]*>\s*)',
            r'\1' + preload,
            html,
            count=1,
            flags=re.I
        )

    # Save template
    template_path = PAGES_TEMPLATES / f'{slug}.html'
    template_path.write_text(html, encoding='utf-8')

    return {
        'slug': slug,
        'url': src_path.stem.replace('__', '/').replace('_', '-') + '/' if slug != 'index' else '/',
        'title': title,
        'h1': h1,
        'meta_description': meta_description,
        'template': f'pages/{slug}.html',
        'content_html': content_html,
    }


# Build pages data
pages_data = []
for src_path in sorted(PAGES_SRC.glob('*.html')):
    if src_path.name == 'pages.json':
        continue
    slug = src_path.stem
    try:
        page = build_page_template(slug, src_path)
        pages_data.append(page)
        print('built template for', slug)
    except Exception as e:
        print('error building', slug, e)

# Build menu from first page (all pages share the same menu)
menu = []
first_html = (PAGES_SRC / 'index.html').read_bytes().decode('utf-8', errors='ignore')
# Try to extract top menu from the HTML
menu_match = re.search(r'<nav[^>]*class=["\'][^"\']*menu[^"\']*["\'][^>]*>(.*?)</nav>', first_html, re.I | re.S)
if menu_match:
    # Extract simple list of links from the menu block
    menu_html = menu_match.group(1)
    # We won't parse deeply; reuse the menu from existing content.json if present
    pass

# Try loading existing menu/site settings
existing = {}
if (DATA / 'content.json').exists():
    try:
        existing = json.loads((DATA / 'content.json').read_text(encoding='utf-8'))
    except Exception:
        pass

site = existing.get('site', {
    'title': 'Экотория',
    'phone': '8 800 201 08 24',
    'email': 'helloekotoriya@yandex.ru',
    'address': 'г. Череповец, ул. Гоголя 56',
    'logo': '/static/upload/CMax/b2e/04g8dplb3dqudbalhuo0ryren6a5dklv.svg',
})

menu = existing.get('menu', [
    {'title': 'Каталог', 'url': '/catalog/', 'children': [
        {'title': 'Наматрасники', 'url': '/catalog/namatrasniki/', 'children': [
            {'title': 'Наматрасники на резинке', 'url': '/catalog/namatrasniki/namatrasniki-na-rezinke/'},
            {'title': 'Наматрасники на молнии', 'url': '/catalog/namatrasniki/namatrasniki-na-molnii/'},
            {'title': 'Непромокаемые наматрасники', 'url': '/catalog/namatrasniki/nepromokaemye-namatrasniki/'},
        ]},
        {'title': 'Одеяла', 'url': '/catalog/odeyala/', 'children': [
            {'title': 'Зимние', 'url': '/catalog/odeyala/zimnie/'},
            {'title': 'Всесезонные', 'url': '/catalog/odeyala/vsesezonnye/'},
            {'title': 'Летние', 'url': '/catalog/odeyala/letnie/'},
        ]},
        {'title': 'Наперники', 'url': '/catalog/naperniki/'},
    ]},
    {'title': 'Оптовым покупателям', 'url': '/help/opt/'},
    {'title': 'Компания', 'url': '/company/', 'children': [
        {'title': 'О компании', 'url': '/company/'},
        {'title': 'Отзывы', 'url': '/company/reviews/'},
        {'title': 'Документы', 'url': '/company/docs/'},
    ]},
    {'title': 'Наше производство', 'url': '/proizvodstvo/'},
    {'title': 'Советы покупателям', 'url': '/blog/'},
    {'title': 'Контакты', 'url': '/contacts/'},
])

socials = existing.get('socials', [
    {'name': 'vk', 'title': 'Вконтакте', 'url': 'https://vk.com/ekotekstyle'},
    {'name': 'telegram', 'title': 'Telegram', 'url': 'https://t.me/+79646746062'},
])

# Сохранить визуальные блоки страниц, созданные в админке-конструкторе:
# они живут только в content.json и иначе были бы потеряны при пересборке.
import blocks as blocks_module
existing_pages = {p['slug']: p for p in existing.get('pages', [])}
for p in pages_data:
    old = existing_pages.get(p['slug'])
    if old and isinstance(old.get('blocks'), list):
        p['blocks'] = old['blocks']
        p['content_html'] = blocks_module.render_blocks(old['blocks'])
# Страницы, созданные в админке (которых нет в static_original), тоже сохраняем
built_slugs = {p['slug'] for p in pages_data}
for old in existing.get('pages', []):
    if old['slug'] not in built_slugs:
        pages_data.append(old)

# Normalize URLs based on pages data
pages_by_slug = {p['slug']: p for p in pages_data}
for p in pages_data:
    # Compute clean URL from slug
    if p['slug'] == 'index':
        p['url'] = '/'
    else:
        p['url'] = '/' + p['slug'].replace('__', '/').replace('_', '-') + '/'

# Rewrite CSS URLs inside static
print('rewriting CSS urls...')

def rewrite_css_url(m):
    url = m.group(1)
    if url.startswith('data:'):
        return m.group(0)
    if url.startswith('/static/'):
        return m.group(0)
    if url.startswith('/'):
        return f'url("/static{url}")'
    return m.group(0)

for css_file in STATIC.rglob('*.css'):
    try:
        text = css_file.read_text(encoding='utf-8', errors='ignore')
        new_text = re.sub(r'url\s*\(\s*["\']?(/[^\)"\'\s]+)["\']?\s*\)', rewrite_css_url, text)
        if new_text != text:
            css_file.write_text(new_text, encoding='utf-8')
    except Exception as e:
        print('css rewrite error', css_file, e)

content = {
    'site': site,
    'pages': pages_data,
    'menu': menu,
    'socials': socials,
}

(DATA / 'content.json').write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
print('templates and content.json created for', len(pages_data), 'pages')

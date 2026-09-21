#!/usr/bin/env python3
"""
Шаблоны страниц конструктора (CN-T9.2): готовые наборы блоков-заготовок.

Каждый пресет — {id, name, description, blocks}; blocks рендерятся обычным
blocks.render_blocks, формат блоков тот же, что в редакторе страниц.
"""
PAGE_TEMPLATES = [
    {
        'id': 'landing',
        'name': 'Лендинг',
        'description': 'Обложка, преимущества, популярные товары, отзывы, заявка и FAQ',
        'blocks': [
            {
                'type': 'banner', 'title': 'Экотория', 'subtitle': 'Наматрасники и постельное бельё от производителя',
                'button': 'Смотреть каталог', 'url': '/catalog/', 'bg': '#b49d84', 'text_color': '#ffffff',
                'height': '320',
            },
            {
                'type': 'features', 'cols': '4', 'title': 'Почему выбирают нас',
                'items': [
                    {'icon': '✔', 'title': 'Своё производство', 'text': 'Шьём в Череповце с 2010 года'},
                    {'icon': '🚚', 'title': 'Быстрая доставка', 'text': 'По всей России от 2 дней'},
                    {'icon': '🛡', 'title': 'Гарантия качества', 'text': 'Возврат в течение 14 дней'},
                    {'icon': '💬', 'title': 'Поддержка', 'text': 'Поможем подобрать размер'},
                ],
            },
            {'type': 'products', 'mode': 'recommendations', 'limit': 8, 'title': 'Популярное'},
            {
                'type': 'reviews', 'title': 'Отзывы покупателей',
                'items': [
                    {'name': 'Марина', 'text': 'Наматрасник сидит идеально, ткань приятная. Закажу ещё!', 'rating': 5},
                    {'name': 'Дмитрий', 'text': 'Доставили за три дня, качество порадовало.', 'rating': 5},
                ],
            },
            {'type': 'form', 'title': 'Оставить заявку', 'button': 'Отправить'},
            {
                'type': 'faq', 'title': 'Частые вопросы',
                'items': [
                    {'q': 'Как подобрать размер наматрасника?',
                     'a': 'Измерьте длину и ширину матраса; берите наматрасник в размер матраса или на резинке.'},
                    {'q': 'Есть ли доставка в регионы?',
                     'a': 'Да, отправляем по всей России; сроки и стоимость зависят от региона.'},
                ],
            },
        ],
    },
    {
        'id': 'showcase',
        'name': 'Витрина',
        'description': 'Заголовок, сетка товаров, текст с картинкой и баннер',
        'blocks': [
            {'type': 'heading', 'level': 'h1', 'text': 'Каталог'},
            {'type': 'products', 'mode': 'source', 'source': 'all', 'limit': 12, 'title': ''},
            {
                'type': 'text_image', 'title': 'О нашем производстве',
                'text': 'Мы шьём наматрасники и постельное бельё из натуральных и гипоаллергенных тканей.\n\nСобственное производство позволяет держать честные цены.',
                'src': '', 'image_pos': 'left',
            },
            {
                'type': 'banner', 'title': 'Скидки на комплекты', 'subtitle': 'При покупке от двух наматрасников',
                'button': 'Выбрать', 'url': '/catalog/', 'bg': '#137333', 'text_color': '#ffffff', 'height': '220',
            },
        ],
    },
    {
        'id': 'contacts',
        'name': 'Контакты',
        'description': 'Заголовок, контакты, карта и форма заявки',
        'blocks': [
            {'type': 'heading', 'level': 'h1', 'text': 'Контакты'},
            {
                'type': 'contacts', 'title': 'Свяжитесь с нами',
                'phone': '8 800 201 08 24', 'email': 'info@ekotoriya.ru',
                'address': 'Москва', 'worktime': 'Пн–Пт 9:00–18:00',
            },
            {'type': 'map', 'title': 'Как нас найти', 'address': 'Москва, ул. Примерная, 1'},
            {'type': 'form', 'title': 'Написать нам', 'button': 'Отправить'},
        ],
    },
]

_TEMPLATE_INDEX = {t['id']: t for t in PAGE_TEMPLATES}


def list_templates():
    """Список пресетов страниц (без изменений — статика)."""
    return list(PAGE_TEMPLATES)


def get_template(template_id):
    """Пресет по id или None."""
    return _TEMPLATE_INDEX.get(str(template_id or ''))

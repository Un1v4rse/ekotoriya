#!/usr/bin/env python3
"""
Курируемый список Google-шрифтов с кириллицей (CN-T7.2).

Статика, без внешних API: family/category/weights используются для
- выбора шрифта в настройках темы (вайтлист: значение применяется
  только если есть в этом списке — защита от инъекции в /theme.css);
- генерации @import url('https://fonts.googleapis.com/css2?family=...').
"""
# family — точное имя семейства в Google Fonts, category: sans|serif|display,
# weights — доступные насыщенности, cyrillic — поддержка кириллицы.
GOOGLE_FONTS = [
    {'family': 'Roboto', 'category': 'sans', 'weights': [300, 400, 500, 700, 900], 'cyrillic': True},
    {'family': 'Open Sans', 'category': 'sans', 'weights': [300, 400, 600, 700, 800], 'cyrillic': True},
    {'family': 'Montserrat', 'category': 'sans', 'weights': [300, 400, 500, 600, 700, 800, 900], 'cyrillic': True},
    {'family': 'Ubuntu', 'category': 'sans', 'weights': [300, 400, 500, 700], 'cyrillic': True},
    {'family': 'PT Sans', 'category': 'sans', 'weights': [400, 700], 'cyrillic': True},
    {'family': 'PT Serif', 'category': 'serif', 'weights': [400, 700], 'cyrillic': True},
    {'family': 'Lora', 'category': 'serif', 'weights': [400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Playfair Display', 'category': 'serif', 'weights': [400, 500, 600, 700, 800, 900], 'cyrillic': True},
    {'family': 'Inter', 'category': 'sans', 'weights': [300, 400, 500, 600, 700, 800], 'cyrillic': True},
    {'family': 'Fira Sans', 'category': 'sans', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Oswald', 'category': 'sans', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Rubik', 'category': 'sans', 'weights': [300, 400, 500, 600, 700, 800, 900], 'cyrillic': True},
    {'family': 'Nunito', 'category': 'sans', 'weights': [300, 400, 600, 700, 800, 900], 'cyrillic': True},
    {'family': 'Source Sans 3', 'category': 'sans', 'weights': [300, 400, 600, 700, 900], 'cyrillic': True},
    {'family': 'Noto Sans', 'category': 'sans', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Noto Serif', 'category': 'serif', 'weights': [400, 600, 700], 'cyrillic': True},
    {'family': 'Manrope', 'category': 'sans', 'weights': [300, 400, 500, 600, 700, 800], 'cyrillic': True},
    {'family': 'Bebas Neue', 'category': 'display', 'weights': [400], 'cyrillic': True},
    {'family': 'Comfortaa', 'category': 'display', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Jost', 'category': 'sans', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Mulish', 'category': 'sans', 'weights': [300, 400, 600, 700, 800, 900], 'cyrillic': True},
    {'family': 'PT Sans Narrow', 'category': 'sans', 'weights': [400, 700], 'cyrillic': True},
    {'family': 'Roboto Condensed', 'category': 'sans', 'weights': [300, 400, 700], 'cyrillic': True},
    {'family': 'Roboto Slab', 'category': 'serif', 'weights': [300, 400, 500, 600, 700, 800, 900], 'cyrillic': True},
    {'family': 'Merriweather', 'category': 'serif', 'weights': [300, 400, 700, 900], 'cyrillic': True},
    {'family': 'IBM Plex Sans', 'category': 'sans', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'IBM Plex Serif', 'category': 'serif', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Spectral', 'category': 'serif', 'weights': [300, 400, 500, 600, 700, 800], 'cyrillic': True},
    {'family': 'Cormorant Garamond', 'category': 'serif', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
    {'family': 'Blogger Sans', 'category': 'sans', 'weights': [300, 400, 500, 700], 'cyrillic': True},
    {'family': 'Marck Script', 'category': 'display', 'weights': [400], 'cyrillic': True},
    {'family': 'Bad Script', 'category': 'display', 'weights': [400], 'cyrillic': True},
    {'family': 'Prata', 'category': 'serif', 'weights': [400], 'cyrillic': True},
    {'family': 'Tenor Sans', 'category': 'sans', 'weights': [400], 'cyrillic': True},
    {'family': 'Underdog', 'category': 'display', 'weights': [400], 'cyrillic': True},
    {'family': 'Yanone Kaffeesatz', 'category': 'sans', 'weights': [300, 400, 500, 600, 700], 'cyrillic': True},
]

# Индекс по имени в нижнем регистре для быстрой проверки вайтлиста.
_FAMILY_INDEX = {f['family'].lower(): f for f in GOOGLE_FONTS}


def list_fonts(query=None):
    """Список шрифтов; query — необязательный фильтр по family (case-insensitive)."""
    if not query:
        return list(GOOGLE_FONTS)
    q = str(query).strip().lower()
    return [f for f in GOOGLE_FONTS if q in f['family'].lower()]


def find_font(family):
    """Карточка шрифта по имени (точное совпадение, case-insensitive) или None."""
    if not family or not isinstance(family, str):
        return None
    return _FAMILY_INDEX.get(family.strip().lower())


def is_valid_family(family):
    """True, если family входит в курируемый список (защита от инъекции в CSS)."""
    return find_font(family) is not None


def import_url(family):
    """URL Google Fonts CSS2 для @import (только для семейств из вайтлиста)."""
    card = find_font(family)
    if card is None:
        return ''
    weights = ';'.join(str(w) for w in card['weights'])
    name = card['family'].replace(' ', '+')
    return f"https://fonts.googleapis.com/css2?family={name}:wght@{weights}&display=swap"

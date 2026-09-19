# -*- coding: utf-8 -*-
"""Формула итоговой цены (PR-2, PR-4, PR-6) и приоритет скидок (PR-3).

    база = discount_price (>0), иначе price
    процент:   итог = floor(база × (1 − admin/100)), минимум 1 руб.
    фикс. сумма: итог = база − сумма, минимум 1 руб.
Округление — в пользу клиента (math.floor). Скидка МП в итог не входит
(она уже в discount_price); суммирование wb+admin отсутствует (PR-5).
"""
import pytest

import pricing
from db import db
from models import Discount


def test_no_discounts():
    assert pricing.final_price({'price': 1000}) == 1000


def test_zero_price():
    assert pricing.final_price({'price': 0}) == 0
    assert pricing.final_price({}) == 0


def test_base_from_discount_price():
    """База — цена МП со скидкой, если она задана (PR-2)."""
    assert pricing.base_price({'price': 1000, 'discount_price': 900}) == 900
    assert pricing.base_price({'price': 1000, 'discount_price': 0}) == 1000


def test_mp_discount_does_not_stack():
    """Скидка МП сама по себе итог не меняет — она уже в discount_price."""
    assert pricing.final_price({'price': 1000, 'wb_discount_percent': 30}) == 1000
    assert pricing.final_price({'price': 1000, 'discount_price': 700,
                                'wb_discount_percent': 30}) == 700


@pytest.mark.parametrize('admin,expected', [
    (10, 810),    # floor(900 × 0.9)
    (15, 765),    # floor(900 × 0.85)
    (99, 9),      # floor(900 × 0.01)
    (0, 900),
])
def test_percent_discount_on_discounted_base(admin, expected):
    assert pricing.final_price({'price': 1000, 'discount_price': 900,
                                'admin_discount_percent': admin}) == expected


def test_floor_rounding_in_client_favor():
    """Округление в пользу клиента: 999 × 0.9 = 899.1 → 899 (PR-6)."""
    assert pricing.final_price({'price': 999, 'admin_discount_percent': 10}) == 899
    assert pricing.final_price({'price': 100, 'admin_discount_percent': 33}) == 67  # 67.0


def test_percent_clamped_to_99():
    assert pricing.final_price({'price': 1000, 'admin_discount_percent': 150}) == 10
    assert pricing.final_price({'price': 1000, 'admin_discount_percent': -5}) == 1000


def test_fixed_amount_via_compute():
    for amount, expected in [(150, 850), (0, 1000), (999, 1), (5000, 1)]:
        assert pricing.compute_final_price({'price': 1000}, discount_type='amount',
                                           amount=amount) == expected


def test_fixed_amount_beats_percent_when_type_amount():
    p = {'price': 1000, 'admin_discount_type': 'amount', 'admin_discount_amount': 200,
         'admin_discount_percent': 0}
    assert pricing.final_price(p) == 800


# --- Приоритет правил (PR-3): индивидуальная → category → source → all ---

def _rule(scope, value, percent, active=True):
    r = Discount(scope=scope, scope_value=value, discount_type='percent',
                 value=percent, active=active)
    db.session.add(r)
    db.session.commit()
    return r


def test_rule_priority_individual_beats_all():
    _rule('all', '', 50)
    p = {'price': 1000, 'admin_discount_percent': 10}
    assert pricing.final_price(p) == 900  # индивидуальная 10%, не 50


def test_rule_priority_category_over_source_over_all():
    _rule('all', '', 50)
    _rule('source', 'wb', 30)
    _rule('category', 'Наматрасники', 20)
    p = {'price': 1000, 'source': 'wb', 'category': 'Наматрасники'}
    assert pricing.final_price(p) == 800  # category 20%
    p2 = {'price': 1000, 'source': 'wb', 'category': 'Другое'}
    assert pricing.final_price(p2) == 700  # source 30%
    p3 = {'price': 1000, 'source': 'ozon', 'category': ''}
    assert pricing.final_price(p3) == 500  # all 50%


def test_inactive_rule_ignored():
    _rule('all', '', 50, active=False)
    assert pricing.final_price({'price': 1000}) == 1000


def test_enrich_product_adds_badge():
    rules = []
    p = pricing.enrich_product({'price': 1000, 'discount_price': 900,
                                'admin_discount_percent': 10}, rules)
    assert p['final_price'] == 810
    assert p['site_discount_badge'] == 10  # (900−810)/900
    p2 = pricing.enrich_product({'price': 1000}, rules)
    assert p2['final_price'] == 1000
    assert p2['site_discount_badge'] == 0

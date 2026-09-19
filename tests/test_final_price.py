# -*- coding: utf-8 -*-
"""Baseline формулы цены (PR-2, этап 1 — старая формула, до этапа 2).

Формула этапа 1: round(база × (1 − min(wb + admin, 99)/100)).
В этапе 2 формула меняется — эти тесты зафиксируют поведение-референс
и будут переписаны под новую формулу (база = discount_price или price;
итог = база × (1 − admin/100), округление в пользу клиента).
"""
import pytest

from wb_sync import final_price


def test_no_discounts():
    assert final_price({'price': 1000}) == 1000


def test_zero_price():
    assert final_price({'price': 0}) == 0
    assert final_price({}) == 0


@pytest.mark.parametrize('wb,admin,expected', [
    (10, 0, 900),      # round(1000 * 0.90)
    (0, 15, 850),      # round(1000 * 0.85)
    (10, 15, 750),     # round(1000 * 0.75)
    (99, 50, 10),      # сумма ограничена 99: round(1000 * 0.01)
    (100, 0, 10),      # wb-скидка тоже ограничена 99
    (33, 0, 670),      # round(1000 * 0.67)
    (1, 1, 980),       # round(1000 * 0.98)
])
def test_discount_combinations(wb, admin, expected):
    assert final_price({
        'price': 1000,
        'wb_discount_percent': wb,
        'admin_discount_percent': admin,
    }) == expected


def test_missing_discount_fields_treated_as_zero():
    assert final_price({'price': 500, 'wb_discount_percent': 20}) == 400
    assert final_price({'price': 500, 'admin_discount_percent': 20}) == 400

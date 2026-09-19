# -*- coding: utf-8 -*-
"""Пересчёт рекомендаций по расписанию (RS-5).

Запуск из cron (например, раз в час):

    python jobs/recompute_recommendations.py

Пишет результаты в таблицу recommendations (глобальные popular +
персональные по каждому клиенту). Сайт при этом может читать рекомендации
напрямую через модуль (score-based) — таблица нужна для быстрой выдачи
и истории.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import standalone_app  # noqa: E402


def main():
    import recommendations
    app = standalone_app()
    with app.app_context():
        count = recommendations.recompute_all()
    print(f'[recommendations] пересчитано записей: {count}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

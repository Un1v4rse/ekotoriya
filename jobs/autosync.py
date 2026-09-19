# -*- coding: utf-8 -*-
"""Периодическая синхронизация с маркетплейсами (AR-4, WB-2, OZ-5).

Запускается из cron/планировщика Windows (не из потока веб-сервера):

    python jobs/autosync.py                 # по расписанию из настроек
    python jobs/autosync.py --force-catalog # принудительно полный синк
    python jobs/autosync.py --force-light   # принудительно лёгкий синк

Настройки читаются из БД (site_settings, ключи `site.wb_autosync`,
`site.ozon_autosync`): {"enabled": true, "catalog_hours": 6, "light_minutes": 30}
Моменты последних запусков хранятся там же, в ключе `site.mp_autosync_state`:
    {"wb": {"last_catalog": ..., "last_light": ...}, "ozon": {...}}
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402
import wb_sync  # noqa: E402

DEFAULTS = {
    'enabled': False,
    'catalog_hours': 6,     # полная синхронизация (карточки + цены + остатки)
    'light_minutes': 30,    # лёгкая (цены + остатки)
}
STATE_KEY = 'site.mp_autosync_state'
SOURCES = ('wb', 'ozon')


def get_settings(source):
    settings = dict(DEFAULTS)
    try:
        saved = (storage.get_setting('site') or {}).get(f'{source}_autosync') or {}
        settings.update(saved)
    except Exception:
        pass
    return settings


def get_state():
    try:
        raw = storage.get_setting(STATE_KEY) or '{}'
        return json.loads(raw)
    except Exception:
        return {}


def save_state(state):
    try:
        storage.set_setting(STATE_KEY, json.dumps(state, ensure_ascii=False))
    except Exception:
        pass


def run_source(source, force_catalog=False, force_light=False):
    """Один проход по источнику. Возвращает True, если что-то синхронизировали."""
    s = get_settings(source)
    state = get_state().get(source) or {}
    now = time.time()
    if not s.get('enabled') and not (force_catalog or force_light):
        print(f'[autosync] {source}: авто-синхронизация выключена — пропуск')
        return False

    catalog_every = max(1, int(s.get('catalog_hours', 6))) * 3600
    light_every = max(5, int(s.get('light_minutes', 30))) * 60
    last_catalog = float(state.get('last_catalog') or 0)
    last_light = float(state.get('last_light') or 0)

    full_state = get_state()
    if force_catalog or now - last_catalog >= catalog_every:
        print(f'[autosync] {source}: полная синхронизация (карточки + цены + остатки)...')
        if source == 'wb':
            token = wb_sync.get_wb_token()
            result = wb_sync.sync(token=token, demo=not token)
        else:
            import ozon_sync
            result = ozon_sync.sync()
        state['last_catalog'] = state['last_light'] = time.time()
        full_state[source] = state
        save_state(full_state)
        print(f"[autosync] {source}: полный синк завершён: {result.get('count', 0)} товаров")
        return True
    if force_light or now - last_light >= light_every:
        print(f'[autosync] {source}: лёгкая синхронизация (цены + остатки)...')
        if source == 'wb':
            result = wb_sync.sync_light(token=wb_sync.get_wb_token())
        else:
            import ozon_sync
            result = ozon_sync.sync_light()
        state['last_light'] = time.time()
        full_state[source] = state
        save_state(full_state)
        print(f"[autosync] {source}: лёгкий синк завершён: {result.get('count', 0)} товаров")
        return True
    print(f'[autosync] {source}: по расписанию не требуется')
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Авто-синхронизация маркетплейсов')
    parser.add_argument('--force-catalog', action='store_true', help='принудительно полный синк')
    parser.add_argument('--force-light', action='store_true', help='принудительно лёгкий синк')
    parser.add_argument('--source', choices=SOURCES, help='только один источник')
    args = parser.parse_args(argv)

    sources = (args.source,) if args.source else SOURCES
    for source in sources:
        try:
            run_source(source, force_catalog=args.force_catalog,
                       force_light=args.force_light)
        except Exception as e:
            print(f'[autosync] {source}: ошибка: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Периодическая синхронизация с маркетплейсами (AR-4, WB-2).

Запускается из cron/планировщика Windows (не из потока веб-сервера):

    python jobs/autosync.py                 # по расписанию из настроек
    python jobs/autosync.py --force-catalog # принудительно полный синк
    python jobs/autosync.py --force-light   # принудительно лёгкий синк

Настройки читаются из БД (site_settings, ключ `site.wb_autosync`):
    {"enabled": true, "catalog_hours": 6, "light_minutes": 30}
Моменты последних запусков хранятся там же, в ключе `site.wb_autosync_state`.
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
STATE_KEY = 'site.wb_autosync_state'


def get_settings():
    settings = dict(DEFAULTS)
    try:
        saved = (storage.load_content().get('site', {}) or {}).get('wb_autosync') or {}
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


def main(argv=None):
    parser = argparse.ArgumentParser(description='Авто-синхронизация маркетплейсов')
    parser.add_argument('--force-catalog', action='store_true', help='принудительно полный синк')
    parser.add_argument('--force-light', action='store_true', help='принудительно лёгкий синк')
    args = parser.parse_args(argv)

    s = get_settings()
    state = get_state()
    now = time.time()
    token = wb_sync.get_wb_token()

    if not s.get('enabled') and not (args.force_catalog or args.force_light):
        print('[autosync] авто-синхронизация выключена в настройках — выход')
        return 0

    catalog_every = max(1, int(s.get('catalog_hours', 6))) * 3600
    light_every = max(5, int(s.get('light_minutes', 30))) * 60
    last_catalog = float(state.get('last_catalog') or 0)
    last_light = float(state.get('last_light') or 0)

    if args.force_catalog or now - last_catalog >= catalog_every:
        print('[autosync] полная синхронизация WB (карточки + цены + остатки)...')
        wb_sync.sync(token=token, demo=not token)
        state['last_catalog'] = state['last_light'] = time.time()
        save_state(state)
        print('[autosync] полный синк завершён')
    elif args.force_light or now - last_light >= light_every:
        print('[autosync] лёгкая синхронизация WB (цены + остатки)...')
        wb_sync.sync_light(token=token)
        state['last_light'] = time.time()
        save_state(state)
        print('[autosync] лёгкий синк завершён')
    else:
        print('[autosync] синхронизация не требуется по расписанию')
    return 0


if __name__ == '__main__':
    sys.exit(main())

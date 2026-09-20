#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Нагрузочный тест TS-4 (Этап 6.3): 20 RPS на / и /catalog/ в течение 30 сек.

Сам поднимает сервер приложения на порту 5057 с временной БД (subprocess),
прогоняет нагрузку потоками, считает TTFB p50/p95/p99 и долю ошибок,
после чего останавливает сервер. Критерий ТЗ: p95 TTFB ≤ 300 мс.

Запуск:
    .venv/Scripts/python.exe -X utf8 scripts/load_test.py
    .venv/Scripts/python.exe -X utf8 scripts/load_test.py --rps 20 --seconds 30 --port 5057
"""
import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PATHS = ['/', '/catalog/']


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def start_server(port, db_path, log_path):
    """app.run в фоновом subprocess с временной SQLite-БД.

    БД — копия боевой data/ekotoriya.db (если есть): страницы/товары/индексы
    реальные, контент-кэш и кэш каталога работают как в проде. Без неё —
    create_all + сид контентом из data/content.json, как у свежего деплоя.
    """
    import shutil
    src = BASE_DIR / 'data' / 'ekotoriya.db'
    seed = ''
    if src.exists():
        shutil.copy2(src, db_path)
    else:
        seed = (
            "import storage as _storage\n"
            "with app.app.app_context():\n"
            "    _storage.save_content(_storage.load_content())\n"
        )
    env = dict(os.environ)
    env['DATABASE_URL'] = f'sqlite:///{db_path}'
    env['SECRET_KEY'] = 'load-test-secret'
    env['CACHE_TYPE'] = 'SimpleCache'
    code = (
        "import app\n"
        "from db import db\n"
        "with app.app.app_context():\n"
        "    db.create_all()\n"
        + seed +
        f"app.app.run(host='127.0.0.1', port={port}, threaded=True)\n"
    )
    log = open(log_path, 'w', encoding='utf-8')
    proc = subprocess.Popen(
        [sys.executable, '-X', 'utf8', '-c', code],
        cwd=str(BASE_DIR), env=env, stdout=log, stderr=subprocess.STDOUT)
    # Ждём readiness (до 30 c)
    url = f'http://127.0.0.1:{port}/'
    for _ in range(300):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return proc, log
        except Exception:
            time.sleep(0.1)
    proc.kill()
    log.close()
    raise RuntimeError(f'Сервер не поднялся за 30 секунд, лог: {log_path}')


def worker(base, worker_id, rps_per_worker, seconds, results, errors, stop):
    """Поток: равномерный темп rps_per_worker на чередующиеся / и /catalog/."""
    interval = 1.0 / rps_per_worker
    deadline = time.monotonic() + seconds
    i = 0
    session_cookie = None
    while time.monotonic() < deadline and not stop.is_set():
        url = base + PATHS[i % len(PATHS)]
        i += 1
        headers = {'Cookie': session_cookie} if session_cookie else {}
        req = urllib.request.Request(url, headers=headers)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                # TTFB: время до получения заголовков ответа
                ttfb = (time.monotonic() - t0) * 1000
                resp.read()  # дочитываем тело, чтобы соединение не висело
                results.append(ttfb)
                for h, v in resp.headers.items():
                    if h.lower() == 'set-cookie' and session_cookie is None:
                        session_cookie = v.split(';')[0]
        except Exception:
            errors.append(url)
        # выравнивание темпа
        delay = interval - (time.monotonic() - t0)
        if delay > 0:
            time.sleep(delay)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rps', type=int, default=20)
    ap.add_argument('--seconds', type=int, default=30)
    ap.add_argument('--port', type=int, default=5057)
    ap.add_argument('--workers', type=int, default=10)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='ekotoriya-load-'))
    db_path = tmp / 'load.db'
    log_path = tmp / 'server.log'
    proc, log = start_server(args.port, db_path, log_path)
    print(f'[load] сервер поднят на :{args.port}, {args.rps} RPS на '
          f'{args.workers} потоках, {args.seconds} c')
    base = f'http://127.0.0.1:{args.port}'
    results, errors = [], []
    stop = threading.Event()
    rps_per_worker = max(1, args.rps // args.workers)
    threads = [threading.Thread(target=worker, args=(
        base, w, rps_per_worker, args.seconds, results, errors, stop))
        for w in range(args.workers)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()
    elapsed = time.monotonic() - t0
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

    results.sort()
    total = len(results) + len(errors)
    p50 = percentile(results, 0.50)
    p95 = percentile(results, 0.95)
    p99 = percentile(results, 0.99)
    err_rate = (len(errors) / total * 100) if total else 0.0
    print(f'[load] запросов: {total} (ок {len(results)}, ошибок {len(errors)}, '
          f'{err_rate:.2f}%) за {elapsed:.1f} c — фактически {len(results) / elapsed:.1f} RPS')
    print(f'[load] TTFB p50 = {p50:.1f} мс, p95 = {p95:.1f} мс, p99 = {p99:.1f} мс')
    ok = p95 <= 300 and err_rate < 1.0
    print(f'[load] критерий TS-4 (p95 ≤ 300 мс, ошибок < 1%): '
          f'{"ПРОЙДЕН" if ok else "НЕ ПРОЙДЕН"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

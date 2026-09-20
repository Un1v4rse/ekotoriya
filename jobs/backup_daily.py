# -*- coding: utf-8 -*-
"""Ежедневный бэкап (Этап 6.4, AR-8/AD-5): SQLite + content.json → zip.

Копирует файл БД (WAL: сначала checkpoint) и полный контент в
backups/daily/YYYY-MM-DD.zip, хранит последние 14 дней (ротация).

Запуск из cron/планировщика (ежедневно, ночью):

    python jobs/backup_daily.py            # backups/daily рядом с проектом
    python jobs/backup_daily.py --dir /path/to/backups
"""
import argparse
import json
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402
from db import database_url, standalone_app  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_DIR = BASE_DIR / 'backups' / 'daily'
KEEP_DAYS = 14


def _sqlite_path():
    uri = database_url()
    if uri.startswith('sqlite:///'):
        return Path(uri[len('sqlite:///'):])
    return None


def create_backup(out_dir):
    """Zip с файлом БД (если SQLite) и content.json. Возвращает путь к архиву."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f'{date.today().isoformat()}.zip'

    app = standalone_app()
    with app.app_context():
        db_file = _sqlite_path()
        if db_file and db_file.exists():
            # WAL → сбросить журнал в основной файл перед копированием
            from sqlalchemy import text
            from db import db
            db.session.execute(text('PRAGMA wal_checkpoint(TRUNCATE)'))
            db.session.commit()
        content = storage.load_content()

    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
        if db_file and db_file.exists():
            zf.write(db_file, 'ekotoriya.db')
        zf.writestr('content.json', json.dumps(content, ensure_ascii=False, indent=2))
    return target


def rotate(out_dir, keep_days=KEEP_DAYS):
    """Удалить архивы старше keep_days (по дате в имени файла)."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return []
    from datetime import timedelta
    threshold = date.today() - timedelta(days=keep_days)
    removed = []
    for f in sorted(out_dir.glob('*.zip')):
        try:
            day = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if day < threshold:
            f.unlink()
            removed.append(f.name)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=str(DEFAULT_BACKUP_DIR),
                    help='каталог для ежедневных архивов (default: backups/daily)')
    ap.add_argument('--keep', type=int, default=KEEP_DAYS,
                    help='сколько дней хранить (default: 14)')
    args = ap.parse_args()

    target = create_backup(args.dir)
    removed = rotate(args.dir, args.keep)
    size_kb = target.stat().st_size // 1024
    print(f'[backup] создан: {target} ({size_kb} КБ)')
    if removed:
        print(f'[backup] ротация: удалено {len(removed)} архивов старше {args.keep} дней')


if __name__ == '__main__':
    main()

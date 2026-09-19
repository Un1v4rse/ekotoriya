#!/usr/bin/env python3
"""
Подключение к БД (AR-1, AR-2).

По умолчанию — SQLite (data/ekotoriya.db). Для PostgreSQL задайте
переменную окружения DATABASE_URL, например:
    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/ekotoriya

Модуль не зависит от app.py (app.py импортирует его, а не наоборот),
поэтому его можно использовать из скриптов (миграции, cron-задачи).
"""
import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = Path(__file__).resolve().parent

db = SQLAlchemy()


def database_url():
    """URL БД: env DATABASE_URL, иначе SQLite-файл в data/."""
    return os.environ.get('DATABASE_URL') or f"sqlite:///{BASE_DIR / 'data' / 'ekotoriya.db'}"


def init_app(app: Flask):
    """Привязка SQLAlchemy к Flask-приложению (SQLite в WAL-режиме)."""
    app.config.setdefault('SQLALCHEMY_DATABASE_URI', database_url())
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, 'connect')
        def _sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute('PRAGMA busy_timeout=5000')
            cursor.close()

    return app


def standalone_app() -> Flask:
    """Минимальное Flask-приложение с контекстом БД для скриптов и cron-задач."""
    app = Flask(__name__)
    init_app(app)
    return app

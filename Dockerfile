# Экотория — production-образ (Этап 6.4, AR-8)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости (слой кэшируется), потом код
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Персистентные данные: SQLite, image_cache, загруженные файлы (volume в compose)
ENV DATABASE_URL=sqlite:////app/data/ekotoriya.db \
    CACHE_TYPE=SimpleCache \
    FORCE_HTTPS=0

EXPOSE 8000

# gunicorn: 2 воркера (SimpleCache/lockout/rate-limit — in-memory, per-process)
CMD ["gunicorn", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000", \
     "--timeout", "60", "--access-logfile", "-", "app:app"]

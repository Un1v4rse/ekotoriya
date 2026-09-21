#!/usr/bin/env python3
"""
Модели БД (AR-1). Схема таблиц:
    pages, site_settings, products, discounts,
    customers, customer_auth, orders, events,
    recommendations, sync_logs, admin_users, audit_log

Для обратной совместимости с шаблонами/админкой у Page и Product
есть методы to_content_dict()/to_product_dict(), воспроизводящие
формат старых JSON-файлов (data/content.json, data/wb_products.json).
"""
from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint

from db import db


def utcnow():
    return datetime.now(timezone.utc)


class Page(db.Model):
    __tablename__ = 'pages'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(190), unique=True, nullable=False, index=True)
    url = db.Column(db.String(500), nullable=False, default='/')
    title = db.Column(db.String(500), default='')
    h1 = db.Column(db.String(500), default='')
    meta_description = db.Column(db.Text, default='')
    meta_keywords = db.Column(db.String(500), default='')
    canonical = db.Column(db.String(500), default='')
    template = db.Column(db.String(300), default='')
    blocks = db.Column(db.JSON, nullable=True)          # массив блоков конструктора
    content_html = db.Column(db.Text, default='')       # производный рендер опубликованных блоков
    draft_blocks = db.Column(db.JSON, nullable=True)    # черновик (CN-7), NULL = нет
    published_blocks = db.Column(db.JSON, nullable=True)  # опубликованные блоки (CN-T10)
    settings = db.Column(db.JSON, nullable=True)        # настройки страницы (CN-T5), NULL = нет
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_content_dict(self):
        return {
            'slug': self.slug,
            'url': self.url,
            'title': self.title or '',
            'h1': self.h1 or '',
            'meta_description': self.meta_description or '',
            'meta_keywords': self.meta_keywords or '',
            'canonical': self.canonical or '',
            'template': self.template or '',
            'blocks': self.blocks if isinstance(self.blocks, list) else None,
            'published_blocks': self.published_blocks if isinstance(self.published_blocks, list) else None,
            'settings': self.settings if isinstance(self.settings, dict) else None,
            'content_html': self.content_html or '',
            'updated_at': self.updated_at.isoformat() if self.updated_at else '',
        }


class PageVersion(db.Model):
    """Снапшоты версий страницы (CN-T10.2): блоки + мета-поля, максимум 10 на страницу."""
    __tablename__ = 'page_versions'

    id = db.Column(db.Integer, primary_key=True)
    page_slug = db.Column(db.String(190), nullable=False, index=True, default='')
    blocks = db.Column(db.JSON, nullable=True)          # массив блоков на момент версии
    page_fields = db.Column(db.JSON, nullable=True)     # title/h1/meta_*/canonical/url
    label = db.Column(db.String(120), default='')
    created_by = db.Column(db.String(120), default='')
    created_at = db.Column(db.DateTime, default=utcnow)


class BlockTemplate(db.Model):
    """«Мои блоки» (CN-T9.1): сохранённые админом блоки для переиспользования."""
    __tablename__ = 'block_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default='')
    category = db.Column(db.String(60), default='')
    block = db.Column(db.JSON, nullable=True)           # dict-описание блока
    created_at = db.Column(db.DateTime, default=utcnow)


class SiteSetting(db.Model):
    """Ключ-значение (JSON) для site/menu/socials и прочих настроек сайта."""
    __tablename__ = 'site_settings'

    key = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Product(db.Model):
    """Единая схема товара для WB и Ozon (OZ-3), source='wb'|'ozon'|'manual'."""
    __tablename__ = 'products'
    __table_args__ = (
        UniqueConstraint('source', 'nm_id', name='uq_products_source_nmid'),
    )

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(20), nullable=False, default='wb', index=True)
    nm_id = db.Column(db.BigInteger, nullable=False, default=0, index=True)
    name = db.Column(db.String(1000), default='')
    brand = db.Column(db.String(300), default='')
    vendor_code = db.Column(db.String(300), default='')
    category = db.Column(db.String(300), default='', index=True)
    price = db.Column(db.Integer, default=0)                # базовая цена МП
    discount_price = db.Column(db.Integer, default=0)       # цена МП со скидкой
    mp_discount_percent = db.Column(db.Integer, default=0)  # скидка самого МП
    admin_discount_percent = db.Column(db.Integer, default=0)
    admin_discount_type = db.Column(db.String(10), default='percent')  # percent|amount (PR-4)
    admin_discount_amount = db.Column(db.Integer, default=0)           # фикс. сумма, руб.
    photo = db.Column(db.Text, default='')
    url = db.Column(db.Text, default='')
    stock = db.Column(db.Integer, default=0)
    visible = db.Column(db.Boolean, default=True, index=True)  # WB-5/OZ-4
    rating = db.Column(db.Float, nullable=True)
    review_count = db.Column(db.Integer, default=0)
    extra = db.Column(db.JSON, nullable=True)               # характеристики из API (CT-2)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_product_dict(self):
        """Формат, совместимый со старым data/wb_products.json (ключи те же)."""
        d = {
            'id': self.nm_id or self.id,
            'nm_id': self.nm_id,
            'source': self.source,
            'name': self.name or '',
            'brand': self.brand or '',
            'vendor_code': self.vendor_code or '',
            'category': self.category or '',
            'price': self.price or 0,
            'discount_price': self.discount_price or 0,
            'wb_discount_percent': self.mp_discount_percent or 0,
            'admin_discount_percent': self.admin_discount_percent or 0,
            'admin_discount_type': self.admin_discount_type or 'percent',
            'admin_discount_amount': self.admin_discount_amount or 0,
            'photo': self.photo or '',
            'url': self.url or '',
            'stock': self.stock or 0,
            'visible': bool(self.visible),
            'updated_at': self.updated_at.isoformat() if self.updated_at else '',
        }
        if self.rating:
            d['rating'] = self.rating
            d['review_rating'] = self.rating
        if self.review_count:
            d['review_count'] = self.review_count
        if isinstance(self.extra, dict):
            d['extra'] = self.extra
        return d


class Discount(db.Model):
    """Скидки (PR-3…PR-7): scope=product|category|source|all."""
    __tablename__ = 'discounts'

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(20), nullable=False, default='product')
    scope_value = db.Column(db.String(300), default='')     # nm_id / категория / wb|ozon / ''
    discount_type = db.Column(db.String(10), nullable=False, default='percent')  # percent|amount
    value = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.String(120), default='')
    created_at = db.Column(db.DateTime, default=utcnow)


class Customer(db.Model):
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(300), default='')
    phone = db.Column(db.String(30), default='', index=True)   # нормализованный, уникальный
    email = db.Column(db.String(300), default='', index=True)
    password_hash = db.Column(db.String(300), default='')      # LC-1 (хэш Werkzeug)
    consent_pd = db.Column(db.Boolean, default=False)          # CL-6
    consent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    last_activity_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name or '',
            'phone': self.phone or '',
            'email': self.email or '',
            'consent_pd': bool(self.consent_pd),
            'created_at': self.created_at.isoformat() if self.created_at else '',
            'updated_at': self.updated_at.isoformat() if self.updated_at else '',
            'last_activity_at': self.last_activity_at.isoformat() if self.last_activity_at else '',
        }


class CustomerAuth(db.Model):
    """Сессии/токены клиентов (LC-2)."""
    __tablename__ = 'customer_auth'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False, index=True)
    token = db.Column(db.String(120), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)


class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False, index=True)
    name = db.Column(db.String(300), default='')
    phone = db.Column(db.String(30), default='')
    email = db.Column(db.String(300), default='')
    product_id = db.Column(db.String(60), default='')       # строка: nm_id обоих источников
    product_name = db.Column(db.String(1000), default='')
    price = db.Column(db.Integer, default=0)
    qty = db.Column(db.Integer, default=1)                  # LC-4
    status = db.Column(db.String(30), default='new', index=True)
    source = db.Column(db.String(20), default='site')       # site|basket|oneclick
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'name': self.name or '',
            'phone': self.phone or '',
            'email': self.email or '',
            'product_id': self.product_id or '',
            'product_name': self.product_name or '',
            'price': self.price or 0,
            'qty': self.qty or 1,
            'status': self.status or 'new',
            'source': self.source or 'site',
            'created_at': self.created_at.isoformat() if self.created_at else '',
        }


class Event(db.Model):
    """События (RS-1): view|like|order|favorite|search."""
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=True, index=True)
    session_id = db.Column(db.String(80), default='', index=True)
    product_id = db.Column(db.String(60), default='', index=True)
    product_name = db.Column(db.String(1000), default='')
    query = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime, default=utcnow, index=True)


class Recommendation(db.Model):
    """Пересчитанные рекомендации (RS-5): customer_id=NULL — глобальные (популярное)."""
    __tablename__ = 'recommendations'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=True, index=True)
    kind = db.Column(db.String(20), default='popular')      # popular|personal|similar
    product_id = db.Column(db.String(60), default='')
    score = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=utcnow)


class SyncLog(db.Model):
    __tablename__ = 'sync_logs'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(20), default='wb', index=True)  # wb|ozon
    status = db.Column(db.String(20), default='ok')
    message = db.Column(db.Text, default='')
    count = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    def to_dict(self):
        return {
            'time': self.created_at.isoformat(timespec='seconds') if self.created_at else '',
            'source': self.source,
            'status': self.status,
            'message': self.message or '',
            'count': self.count,
        }


class AdminUser(db.Model):
    __tablename__ = 'admin_users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(30), default='superadmin')   # AD-2
    must_change_password = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class AuditLog(db.Model):
    """Аудит-лог действий админов (AD-4, PR-7)."""
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    admin_user = db.Column(db.String(120), default='', index=True)
    action = db.Column(db.String(120), default='')
    object_type = db.Column(db.String(60), default='')
    object_id = db.Column(db.String(120), default='')
    detail = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'admin_user': self.admin_user or '',
            'action': self.action or '',
            'object_type': self.object_type or '',
            'object_id': self.object_id or '',
            'detail': self.detail,
            'created_at': self.created_at.isoformat(timespec='seconds') if self.created_at else '',
        }

"""Column types that behave the same on SQLite, PostgreSQL and Oracle (19c+)."""

import json
from datetime import timezone

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.dialects.oracle import CLOB, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class JSONText(TypeDecorator):
    """JSON documents: JSONB on PostgreSQL, JSON text in a CLOB on Oracle (19c has no native JSON type), JSON on
    SQLite."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        if dialect.name == "oracle":
            return dialect.type_descriptor(CLOB())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if dialect.name == "oracle" and value is not None:
            return json.dumps(value, ensure_ascii=False)
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "oracle" and value is not None:
            return json.loads(value)
        return value


class NonNullText(TypeDecorator):
    """Text that is never None to the application. Oracle stores '' as NULL, so columns of this type are nullable in
    the database and NULL reads back as ''."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value or None

    def process_result_value(self, value, dialect):
        return value or ""


class UTCDateTime(TypeDecorator):
    """A UTC timestamp, always read back timezone-aware. Oracle's DateTime maps to DATE (whole seconds, no zone), so
    it gets TIMESTAMP WITH TIME ZONE; SQLite keeps no zone, so its values are marked UTC on reading."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "oracle":
            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

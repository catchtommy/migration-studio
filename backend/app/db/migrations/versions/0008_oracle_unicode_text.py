"""Oracle: long text in NCLOB instead of CLOB, so it is stored in Unicode whatever the database's character set.

A CLOB uses the database character set; on databases with a single-byte one (WE8MSWIN1252, WE8ISO8859P1) characters
it can't hold were silently replaced with '¿'. This converts every CLOB column of the studio's tables to NCLOB
(Oracle can't modify CLOB to NCLOB in place: a new column is added, filled, and renamed over the old one). Text
already saved with '¿' can't be recovered; it is right again once regenerated. Nothing to do on other databases.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "oracle":
        return
    from app.db.models import Base
    ours = {t.name.upper(): t.name for t in Base.metadata.tables.values()}
    rows = bind.execute(sa.text("SELECT table_name, column_name, nullable FROM user_tab_columns "
                                "WHERE data_type = 'CLOB' ORDER BY table_name, column_id")).all()
    for table, column, nullable in rows:
        if table.upper() not in ours:
            continue
        t, c, tmp = _quote(table), _quote(column), _quote(f"{column[:24]}_NCL")
        bind.execute(sa.text(f"ALTER TABLE {t} ADD ({tmp} NCLOB)"))
        bind.execute(sa.text(f"UPDATE {t} SET {tmp} = TO_NCLOB({c})"))
        bind.execute(sa.text(f"ALTER TABLE {t} DROP COLUMN {c}"))
        bind.execute(sa.text(f"ALTER TABLE {t} RENAME COLUMN {tmp} TO {c}"))
        if nullable == "N":
            bind.execute(sa.text(f"ALTER TABLE {t} MODIFY ({c} NOT NULL)"))


def downgrade() -> None:
    pass  # NCLOB holds everything CLOB did; nothing to undo

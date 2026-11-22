"""Runs from before runs recorded their scope have none stored: they are feature migrations, and now say so. A query
comparing the scope with 'operation' then finds them too (resetting a feature found none of its older runs).

The code still reads a missing scope as a migration (Run.scope's documented meaning). Workflows keep None for
'operation': that is how they store it on purpose (workflow_store.create).

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE runs SET scope = 'operation' WHERE scope IS NULL"))


def downgrade() -> None:
    pass  # which runs had no scope isn't kept: 'operation' is what they meant anyway

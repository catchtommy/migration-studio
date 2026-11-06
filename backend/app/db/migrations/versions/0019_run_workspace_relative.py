"""Runs keep their workspace relative to the data folder, not as an absolute path of the server that started them.
Every workspace is workspaces/<run id>, so this also corrects rows whose path is out of date.

Revision ID: 0019
Revises: 0018
"""

import os

import sqlalchemy as sa
from alembic import op

from app.config import settings

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE runs SET workspace = 'workspaces/' || id WHERE workspace IS NOT NULL")


def downgrade() -> None:
    op.get_bind().execute(sa.text("UPDATE runs SET workspace = :root || id WHERE workspace IS NOT NULL"),
                          {"root": f"{settings.workspaces_dir}{os.sep}"})

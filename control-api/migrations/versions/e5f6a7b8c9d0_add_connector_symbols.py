"""add connector.symbols_json: scope a connector to a symbol group

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'connector',
        sa.Column('symbols_json', sqlmodel.sql.sqltypes.AutoString(),
                  nullable=False, server_default='[]'),
    )


def downgrade() -> None:
    op.drop_column('connector', 'symbols_json')

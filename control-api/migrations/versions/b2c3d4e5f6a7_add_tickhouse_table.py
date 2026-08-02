"""add tickhouse table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tickhouse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('location', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('os', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('profile', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('spec_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('last_command_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id']),
        sa.ForeignKeyConstraint(['last_command_id'], ['command.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tickhouse_tenant_id'), 'tickhouse', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_tickhouse_name'), 'tickhouse', ['name'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tickhouse_name'), table_name='tickhouse')
    op.drop_index(op.f('ix_tickhouse_tenant_id'), table_name='tickhouse')
    op.drop_table('tickhouse')

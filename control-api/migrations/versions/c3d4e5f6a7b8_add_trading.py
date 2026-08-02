"""add trading: can_trade, order, position

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('can_trade', sa.Boolean(), nullable=False,
                                    server_default=sa.false()))
    op.create_table(
        'order',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('symbol', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('side', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('order_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('limit_price', sa.Float(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('route', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('fill_price', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_order_tenant_id'), 'order', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_order_symbol'), 'order', ['symbol'], unique=False)
    op.create_index(op.f('ix_order_user_email'), 'order', ['user_email'], unique=False)
    op.create_table(
        'position',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('avg_price', sa.Float(), nullable=False),
        sa.Column('realized_pnl', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_position_tenant_id'), 'position', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_position_symbol'), 'position', ['symbol'], unique=False)


def downgrade() -> None:
    op.drop_table('position')
    op.drop_index(op.f('ix_order_user_email'), table_name='order')
    op.drop_index(op.f('ix_order_symbol'), table_name='order')
    op.drop_index(op.f('ix_order_tenant_id'), table_name='order')
    op.drop_table('order')
    op.drop_column('user', 'can_trade')

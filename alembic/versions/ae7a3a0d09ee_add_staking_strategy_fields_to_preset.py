"""Add staking strategy fields to preset

Revision ID: ae7a3a0d09ee
Revises: ffccf3922c69
Create Date: 2026-02-17 14:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ae7a3a0d09ee'
down_revision: Union[str, Sequence[str], None] = 'ffccf3922c69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add simulate field
    op.add_column('preset', sa.Column('simulate', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    
    # Add staking strategy fields
    op.add_column('preset', sa.Column('staking_strategy', sa.String(), nullable=False, server_default='fixed'))
    op.add_column('preset', sa.Column('percent_risk', sa.Float(), nullable=True))
    op.add_column('preset', sa.Column('kelly_multiplier', sa.Float(), nullable=True))
    op.add_column('preset', sa.Column('max_stake', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('preset', 'max_stake')
    op.drop_column('preset', 'kelly_multiplier')
    op.drop_column('preset', 'percent_risk')
    op.drop_column('preset', 'staking_strategy')
    op.drop_column('preset', 'simulate')

"""add_preset_id_to_bet

Revision ID: ea0e7f5af34a
Revises: 8adc565a7612
Create Date: 2026-02-01 19:07:16.072184

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea0e7f5af34a'
down_revision: Union[str, Sequence[str], None] = '8adc565a7612'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preset_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_bet_preset_id_preset', 'preset', ['preset_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_constraint('fk_bet_preset_id_preset', type_='foreignkey')
        batch_op.drop_column('preset_id')

"""make_timestamps_aware

Revision ID: fd89bb957b2a
Revises: 96715a2531cb
Create Date: 2026-03-13 11:02:10.146843

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd89bb957b2a'
down_revision: Union[str, Sequence[str], None] = '96715a2531cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    tables = [
        'bookmaker', 'mapping', 'notification', 'preset', 'sport', 
        'league', 'event', 'bet', 'market', 'odds', 'presethiddenitem'
    ]
    for table in tables:
        op.alter_column(table, 'created_at',
               existing_type=sa.DateTime(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="created_at AT TIME ZONE 'UTC'")
        op.alter_column(table, 'updated_at',
               existing_type=sa.DateTime(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="updated_at AT TIME ZONE 'UTC'")


def downgrade() -> None:
    """Downgrade schema."""
    tables = [
        'bookmaker', 'mapping', 'notification', 'preset', 'sport', 
        'league', 'event', 'bet', 'market', 'odds', 'presethiddenitem'
    ]
    for table in tables:
        op.alter_column(table, 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=sa.DateTime(),
               existing_nullable=False)
        op.alter_column(table, 'updated_at',
               existing_type=sa.DateTime(timezone=True),
               type_=sa.DateTime(),
               existing_nullable=False)

"""add_captains_to_historical_match_statistics

Revision ID: e8293b1029c1
Revises: c93847291a2b
Create Date: 2026-11-02 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8293b1029c1'
down_revision: Union[str, None] = 'c93847291a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('historical_match_statistics', sa.Column('captain_a', sa.String(length=200), nullable=True))
    op.add_column('historical_match_statistics', sa.Column('captain_b', sa.String(length=200), nullable=True))
    op.add_column('historical_match_statistics', sa.Column('toss_winner_captain', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('historical_match_statistics', 'toss_winner_captain')
    op.drop_column('historical_match_statistics', 'captain_b')
    op.drop_column('historical_match_statistics', 'captain_a')


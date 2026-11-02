"""add_health_timestamps_to_collector_health

Revision ID: c93847291a2b
Revises: b84752146170
Create Date: 2026-11-02 11:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c93847291a2b'
down_revision: Union[str, None] = 'b84752146170'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('collector_health', sa.Column('last_success_utc', sa.DateTime(timezone=True), nullable=True))
    op.add_column('collector_health', sa.Column('last_error_utc', sa.DateTime(timezone=True), nullable=True))
    op.add_column('collector_health', sa.Column('last_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('collector_health', 'last_error')
    op.drop_column('collector_health', 'last_error_utc')
    op.drop_column('collector_health', 'last_success_utc')


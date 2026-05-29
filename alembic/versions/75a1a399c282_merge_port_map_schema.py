"""merge_port_map_schema

Revision ID: 75a1a399c282
Revises: b2c3d4e5f6a7, d5e6f7a8b9c0
Create Date: 2026-05-26 12:41:22.014200

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '75a1a399c282'
down_revision: Union[str, None] = ('b2c3d4e5f6a7', 'd5e6f7a8b9c0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

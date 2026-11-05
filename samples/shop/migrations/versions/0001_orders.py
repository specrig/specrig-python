import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("paid", sa.Boolean, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("orders")

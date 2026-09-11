"""add post media and shared post messages

Revision ID: b7c8d9e0f1a2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = ("a7b8c9d0e1f2", "f6a7b8c9d0e1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=2048), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_post_media_post_id", "post_media", ["post_id"])
    op.add_column("messages", sa.Column("shared_post_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_messages_shared_post_id",
        "messages",
        "posts",
        ["shared_post_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_messages_shared_post_id", "messages", ["shared_post_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_shared_post_id", table_name="messages")
    op.drop_constraint("fk_messages_shared_post_id", "messages", type_="foreignkey")
    op.drop_column("messages", "shared_post_id")
    op.drop_index("ix_post_media_post_id", table_name="post_media")
    op.drop_table("post_media")

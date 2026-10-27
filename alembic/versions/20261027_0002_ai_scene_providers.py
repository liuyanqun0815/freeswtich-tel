"""AI scene: provider configs, system prompt, transfer skill group."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0002"
down_revision: Union[str, None] = "20261021_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> list[sa.Column]:
    return [
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("transfer_skill_group_id", sa.Integer(), nullable=True),
        sa.Column("asr_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("tts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("llm_json", sa.Text(), nullable=False, server_default="{}"),
    ]


def upgrade() -> None:
    # 0001 用当前模型 create_all，新库里这些列已经存在
    existing = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("ai_scene")}
    for column in _columns():
        if column.name not in existing:
            op.add_column("ai_scene", column)


def downgrade() -> None:
    for column in reversed(_columns()):
        op.drop_column("ai_scene", column.name)

"""Keyset list indexes for the enquiry/task lists (P2).

The enquiry keyset order (received_at DESC, id DESC) is already
covered by ix_enquiries_received from the baseline. This adds the
matching task keyset index and an owner-leading enquiry index for
the owner filter.
"""

from __future__ import annotations

from alembic import op

revision = "0002_list_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_tasks_created ON leadops.follow_up_tasks "
        "(created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_enquiries_owner_received ON leadops.enquiries "
        "(owner_id, received_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX leadops.ix_enquiries_owner_received")
    op.execute("DROP INDEX leadops.ix_tasks_created")

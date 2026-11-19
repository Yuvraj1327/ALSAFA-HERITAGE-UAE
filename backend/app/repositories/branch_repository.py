import builtins
import uuid

from sqlalchemy import text

from app.repositories.base import BaseRepository

_BRANCH = "select id, name, sort_order, is_active, deactivated_at, deactivation_reason from public.branches"


class BranchRepository(BaseRepository):
    """Queries only. The caller's BranchScope adds the branch restriction; RLS enforces it again."""

    def list(self, only_branch: uuid.UUID | None) -> list[dict[str, object]]:
        sql = _BRANCH + (" where id = :b" if only_branch else "") + " order by sort_order, name"
        rows = (
            self.session.execute(text(sql), {"b": only_branch} if only_branch else {})
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    def get(self, branch_id: uuid.UUID) -> dict[str, object] | None:
        row = (
            self.session.execute(text(_BRANCH + " where id = :b"), {"b": branch_id})
            .mappings()
            .first()
        )
        return None if row is None else dict(row)

    def dependencies(self, branch_id: uuid.UUID) -> builtins.list[dict[str, object]]:
        """Owner only (checked inside the database function)."""
        rows = (
            self.session.execute(
                text(
                    "select key, label, item_count as count, blocking from public.branch_dependencies(:b)"
                ),
                {"b": branch_id},
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    def set_active(self, branch_id: uuid.UUID, active: bool, reason: str) -> None:
        self.session.execute(
            text("select public.set_branch_active(:b, :a, :r)"),
            {"b": branch_id, "a": active, "r": reason},
        )


__all__ = ["BranchRepository"]

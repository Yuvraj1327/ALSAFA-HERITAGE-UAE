"""Branch activate / deactivate (Owner only). Deactivation is blocked while the branch still has operational dependencies;
the same check runs again atomically inside public.set_branch_active(), so a race cannot slip past it."""

import logging
import uuid

from sqlalchemy.exc import DBAPIError

from app.api.authz import PermissionDeniedError
from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.repositories.branch_repository import BranchRepository
from app.schemas.rbac import BranchDependencies, BranchDependency, BranchOut
from app.services.base import BaseService
from app.services.finance_core import _message, sqlstate

logger = logging.getLogger(__name__)


class BranchService(BaseService):
    def __init__(self, session):  # type: ignore[no-untyped-def]
        super().__init__(session)
        self.repo = BranchRepository(session)

    def _branch(self, id_: uuid.UUID) -> dict[str, object]:
        row = self.repo.get(id_)
        if row is None:
            raise NotFoundError("Branch not found")
        return row

    def dependencies(self, id_: uuid.UUID) -> BranchDependencies:
        branch = self._branch(id_)
        items = [BranchDependency(**r) for r in self.repo.dependencies(id_)]  # type: ignore[arg-type]
        return BranchDependencies(
            branch_id=id_,
            is_active=bool(branch["is_active"]),
            can_deactivate=not any(i.blocking and i.count > 0 for i in items),
            items=items,
        )

    def _run(self, fn):  # type: ignore[no-untyped-def]
        try:
            fn()
            self.session.commit()
        except DBAPIError as exc:
            self.session.rollback()
            state = sqlstate(exc)
            msg = _message(exc)
            if state == "42501":
                raise PermissionDeniedError() from exc
            if state == "23514" and msg.startswith("branch_has_dependencies"):
                raise ConflictError(
                    "This branch still has operational dependencies: "
                    + msg.split(":", 1)[1].strip(),
                    code="branch_has_dependencies",
                ) from exc
            if state == "23514":
                raise UnprocessableError(msg or "The change breaks a rule") from exc
            if state == "P0002":
                raise NotFoundError("Branch not found") from exc
            logger.warning("unmapped database error %s", state)
            raise
        except Exception:
            self.session.rollback()
            raise

    def deactivate(self, id_: uuid.UUID, reason: str) -> BranchOut:
        branch = self._branch(id_)
        if not branch["is_active"]:
            raise ConflictError("The branch is already inactive", code="already_inactive")
        deps = self.dependencies(id_)
        blockers = [i for i in deps.items if i.blocking and i.count > 0]
        if blockers:
            raise ConflictError(
                "This branch still has operational dependencies: "
                + "; ".join(f"{i.label} ({i.count})" for i in blockers),
                code="branch_has_dependencies",
                fields={"dependencies": [i.key for i in blockers]},
            )
        self._run(lambda: self.repo.set_active(id_, False, reason))
        return BranchOut(**self._branch(id_))  # type: ignore[arg-type]

    def activate(self, id_: uuid.UUID, reason: str) -> BranchOut:
        branch = self._branch(id_)
        if branch["is_active"]:
            raise ConflictError("The branch is already active", code="already_active")
        self._run(lambda: self.repo.set_active(id_, True, reason))
        return BranchOut(**self._branch(id_))  # type: ignore[arg-type]

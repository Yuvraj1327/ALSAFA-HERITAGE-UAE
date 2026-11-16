"""Owner edit of a Branch User: service-level rules that need no database (stub repository, mocked Auth admin API)."""

import json
import uuid

import httpx
import pytest

from app.core.exceptions import NotFoundError
from app.core.permissions import Principal, Role
from app.schemas.rbac import UserUpdate
from app.services.user_admin_policy import InvalidUserChange, UserAdminDenied
from app.services.user_provisioning_service import (
    ProvisioningNotConfiguredError,
    UserProvisioningService,
)
from tests.conftest import make_settings

UID, BRANCH = uuid.uuid4(), uuid.uuid4()
STRONG = "Str0ng-Passw0rd!"


class Prof:
    def __init__(self, role: str = "branch") -> None:
        self.user_id, self.role, self.branch_id = UID, role, BRANCH
        self.full_name, self.is_active = "Old Name", True


class Session:
    rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True


class Repo:
    def __init__(self, profile: Prof | None = None, commit_fails: bool = False) -> None:
        self.session, self.profile = Session(), profile
        self.commit_fails, self.updates, self.events = commit_fails, [], []

    def get(self, _id: uuid.UUID) -> Prof | None:
        return self.profile

    def branch_exists_active(self, _id: uuid.UUID) -> bool:
        return True

    def update_branch_user(self, uid: uuid.UUID, values: dict, reason: str) -> None:
        self.updates.append((uid, values, reason))

    def record_user_event(self, *args: object) -> None:
        self.events.append(args)

    def commit(self) -> None:
        if self.commit_fails:
            raise RuntimeError("commit failed")


def actor(role: Role) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        role=role,
        branch_id=BRANCH if role is Role.BRANCH_USER else None,
        full_name="A",
    )


def svc(repo: Repo, calls: list, configured: bool = True) -> UserProvisioningService:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, json.loads(req.content) if req.content else {}))
        return httpx.Response(200, json={"id": str(UID), "email": "old@example.test"})

    extra = (
        {"supabase_url": "https://x.supabase.co", "supabase_service_role_key": "k"}
        if configured
        else {}
    )
    return UserProvisioningService(repo, make_settings(**extra), httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_only_the_owner_may_edit() -> None:
    calls: list = []
    with pytest.raises(UserAdminDenied):
        svc(Repo(Prof()), calls).update_branch_user(
            actor(Role.BRANCH_USER), UID, UserUpdate(full_name="X")
        )
    assert calls == []


def test_unknown_user_and_owner_target() -> None:
    with pytest.raises(NotFoundError):
        svc(Repo(None), []).update_branch_user(actor(Role.OWNER), UID, UserUpdate(full_name="X"))
    with pytest.raises(InvalidUserChange):
        svc(Repo(Prof("owner")), []).update_branch_user(
            actor(Role.OWNER), UID, UserUpdate(full_name="X")
        )


def test_name_only_change_never_touches_auth() -> None:
    calls: list = []
    repo = Repo(Prof())
    svc(repo, calls, configured=False).update_branch_user(
        actor(Role.OWNER), UID, UserUpdate(full_name=" New ")
    )
    assert calls == [] and repo.updates[0][1] == {"full_name": "New"} and repo.events == []


def test_password_reset_requires_auth_configuration_and_sets_must_change() -> None:
    body = UserUpdate(password=STRONG, confirm_password=STRONG)
    with pytest.raises(ProvisioningNotConfiguredError):
        svc(Repo(Prof()), [], configured=False).update_branch_user(actor(Role.OWNER), UID, body)
    calls: list = []
    repo = Repo(Prof())
    svc(repo, calls).update_branch_user(actor(Role.OWNER), UID, body)
    assert ("PUT", {"password": STRONG}) in calls
    assert repo.updates[0][1] == {"must_change_password": True}
    assert repo.events[0][1] == "password_reset"
    assert STRONG not in repr(repo.updates) + repr(repo.events)


def test_email_is_restored_when_the_final_commit_fails() -> None:
    calls: list = []
    repo = Repo(Prof(), commit_fails=True)
    with pytest.raises(RuntimeError):
        svc(repo, calls).update_branch_user(
            actor(Role.OWNER), UID, UserUpdate(email="new@example.test")
        )
    puts = [c[1] for c in calls if c[0] == "PUT"]
    assert puts[0]["email"] == "new@example.test" and puts[-1]["email"] == "old@example.test"
    assert repo.session.rolled_back


def test_password_is_secret_in_the_model() -> None:
    m = UserUpdate(password=STRONG, confirm_password=STRONG)
    assert STRONG not in repr(m) and STRONG not in m.model_dump_json()

"""Owner-driven account provisioning: Auth user (with the Owner's initial password) + profile.

Order: authorize -> validate branch -> create Auth user (Supabase Admin API) -> insert profile as the Owner
(one DB transaction, RLS applies) . If the profile insert fails the Auth user is deleted again, so there is never
an Auth user without a profile created by this flow. No invitation email is involved: the Branch User can sign
in at once and must change the initial password on first login (profiles.must_change_password).

Passwords exist only in memory for the duration of the call and are forwarded to Supabase Auth over HTTPS. They are
never stored in profiles, never logged, never put in audit history, never returned. The service-role key stays
in the backend. Only Branch Users can be created here; Owners are provisioned by an operator
(docs/authentication/OWNER_ACCOUNT_SETUP.md).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from app.core.passwords import validate_new_password
from app.core.permissions import Principal, Role
from app.models.profile import Profile
from app.repositories.profile_repository import ProfileRepository
from app.schemas.rbac import UserCreate, UserUpdate
from app.services.user_admin_policy import (
    InvalidUserChange,
    TargetUser,
    UserAdminAction,
    authorize_user_change,
)

logger = logging.getLogger(__name__)


class ProvisioningNotConfiguredError(ServiceUnavailableError):
    code = "provisioning_not_configured"


class AuthProviderError(AppError):
    status_code, code = 502, "auth_provider_error"


class SupabaseAuthAdmin:
    """Minimal Supabase Auth admin client. Never logs request bodies, responses or the key."""

    def __init__(
        self, base_url: str, service_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base = base_url.rstrip("/") + "/auth/v1"
        self.http = httpx.Client(
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            timeout=15,
            transport=transport,
        )

    def _call(self, method: str, url: str, what: str, **kw: Any) -> dict[str, Any]:
        try:
            resp = self.http.request(method, url, **kw)
        except httpx.HTTPError:
            raise AuthProviderError(f"Could not reach the sign-in service ({what}).") from None
        if resp.status_code >= 400:
            self._raise_for_error(resp, what)
        return resp.json() if resp.content else {}

    @staticmethod
    def _raise_for_error(resp: httpx.Response, what: str) -> None:
        try:
            payload = resp.json()
            error_code = str(payload.get("error_code") or payload.get("code") or "")
            msg = str(payload.get("msg") or payload.get("message") or "")
        except ValueError:
            error_code = msg = ""
        if resp.status_code == 409 or error_code in {"email_exists", "user_already_exists"}:
            raise ConflictError("An account with this email already exists.")
        if resp.status_code == 422 and (error_code == "weak_password" or "password" in msg.lower()):
            raise UnprocessableError(
                "The password was not accepted by the sign-in service.",
                fields={"password": ["Choose a stronger password"]},
            )
        if resp.status_code == 422 and ("already" in msg.lower() or "exist" in msg.lower()):
            raise ConflictError("An account with this email already exists.")
        raise AuthProviderError(f"The sign-in service refused the request ({what}).")

    def create_user(self, email: str, password: str, *, email_confirmed: bool) -> uuid.UUID:
        body = self._call(
            "POST",
            f"{self.base}/admin/users",
            "creating the user",
            json={"email": email, "password": password, "email_confirm": email_confirmed},
        )
        return uuid.UUID(str(body["id"]))

    def update_password(self, user_id: uuid.UUID, password: str) -> None:
        self._call(
            "PUT",
            f"{self.base}/admin/users/{user_id}",
            "changing the password",
            json={"password": password},
        )

    def get_email(self, user_id: uuid.UUID) -> str | None:
        """The account's sign-in email (never a password: Supabase does not expose one)."""
        body = self._call("GET", f"{self.base}/admin/users/{user_id}", "reading the user")
        email = body.get("email")
        return str(email) if email else None

    def update_user(
        self,
        user_id: uuid.UUID,
        *,
        email: str | None = None,
        password: str | None = None,
        email_confirmed: bool = True,
    ) -> None:
        payload: dict[str, Any] = {}
        if email is not None:
            payload.update(email=email, email_confirm=email_confirmed)
        if password is not None:
            payload["password"] = password
        if payload:
            self._call(
                "PUT", f"{self.base}/admin/users/{user_id}", "updating the user", json=payload
            )

    def delete_user(self, user_id: uuid.UUID) -> None:
        self._call("DELETE", f"{self.base}/admin/users/{user_id}", "removing the user")


def build_auth_admin(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> SupabaseAuthAdmin:
    if settings.supabase_url is None or settings.supabase_service_role_key is None:
        raise ProvisioningNotConfiguredError(
            "This action is not configured on the server (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required)."
        )
    return SupabaseAuthAdmin(
        str(settings.supabase_url), settings.supabase_service_role_key.get_secret_value(), transport
    )


class UserProvisioningService:
    def __init__(
        self,
        profiles: ProfileRepository,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.profiles = profiles
        self.settings = settings
        self.transport = transport

    def create_branch_user(self, actor: Principal, data: UserCreate) -> Profile:
        authorize_user_change(
            actor,
            UserAdminAction.CREATE_BRANCH_USER,
            new_role=Role.BRANCH_USER,
            new_branch_id=data.branch_id,
        )
        admin = build_auth_admin(self.settings, self.transport)
        if not self.profiles.branch_exists_active(data.branch_id):
            raise InvalidUserChange("Choose an existing, active branch.")
        user_id = admin.create_user(
            data.email,
            data.password.get_secret_value(),
            email_confirmed=self.settings.provision_email_confirmed,
        )
        try:
            return self.profiles.add_branch_user(user_id, data.full_name, data.branch_id)
        except IntegrityError as exc:
            self.profiles.session.rollback()
            self._compensate(admin, user_id)
            orig = getattr(exc, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None)
            constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
            logger.warning("profile insert failed: sqlstate=%s constraint=%s", sqlstate, constraint)
            if sqlstate == "23505":  # unique_violation: a profile for this user id really exists
                raise ConflictError("This user already has a profile.") from None
            raise  # any other integrity problem is a bug/config issue: 500 with details in the server log
        except BaseException:
            self.profiles.session.rollback()
            self._compensate(admin, user_id)
            raise

    def read_email(self, user_id: uuid.UUID) -> str | None:
        """Email for the Owner's edit form; None when the server has no Auth admin access (the form then hides it)."""
        try:
            return build_auth_admin(self.settings, self.transport).get_email(user_id)
        except (ProvisioningNotConfiguredError, AuthProviderError):
            return None

    def update_branch_user(self, actor: Principal, user_id: uuid.UUID, data: UserUpdate) -> Profile:
        """Owner edit: name, email, branch, active status and/or a new password.

        Database changes are made first but only committed after Supabase Auth accepted the email/password change,
        so a rejected email (duplicate) or weak password leaves everything untouched. If the final commit fails
        after the email was changed, the email is put back. Passwords exist only in this call's memory."""
        authorize_user_change(
            actor, UserAdminAction.UPDATE_PROFILE
        )  # module-level permission first
        profile = self.profiles.get(user_id)
        if profile is None:
            raise NotFoundError("User not found")
        role = Role(profile.role)
        target = TargetUser(profile.user_id, role, profile.is_active)
        if role is not Role.BRANCH_USER:
            raise InvalidUserChange("Only Branch User accounts can be edited here.")

        values: dict[str, object] = {}
        if data.full_name is not None and data.full_name != profile.full_name:
            values["full_name"] = data.full_name
        if data.branch_id is not None and data.branch_id != profile.branch_id:
            authorize_user_change(
                actor, UserAdminAction.ASSIGN_BRANCH, target, new_branch_id=data.branch_id
            )
            if not self.profiles.branch_exists_active(data.branch_id):
                raise InvalidUserChange("Choose an existing, active branch.")
            values["branch_id"] = data.branch_id
        if data.is_active is not None and data.is_active != profile.is_active:
            authorize_user_change(
                actor,
                UserAdminAction.ACTIVATE if data.is_active else UserAdminAction.DEACTIVATE,
                target,
            )
            values["is_active"] = data.is_active

        new_password = data.password.get_secret_value() if data.password else None
        wants_email = data.email is not None
        if wants_email or new_password is not None:
            authorize_user_change(actor, UserAdminAction.RESET_ACCESS, target)
        if not values and not wants_email and new_password is None:
            raise InvalidUserChange("Nothing to change.")

        admin = (
            build_auth_admin(self.settings, self.transport)
            if (wants_email or new_password)
            else None
        )
        old_email = new_email = None
        if admin is not None:
            old_email = admin.get_email(user_id)
            if data.email is not None and data.email != old_email:
                new_email = data.email
            if new_password is not None:
                try:
                    validate_new_password(new_password, new_email or old_email)
                except ValueError as exc:
                    raise UnprocessableError(
                        "The password was not accepted.", fields={"password": [str(exc)]}
                    ) from None
            if new_email is None and new_password is None and not values:
                raise InvalidUserChange("Nothing to change.")
        if new_password is not None:
            values["must_change_password"] = (
                True  # the Owner knows it: the user must choose their own
            )

        reason = "Updated by Owner"
        try:
            self.profiles.update_branch_user(user_id, values, reason)
            if admin is not None and (new_email is not None or new_password is not None):
                admin.update_user(
                    user_id,
                    email=new_email,
                    password=new_password,
                    email_confirmed=self.settings.provision_email_confirmed,
                )
            if new_email is not None:
                self.profiles.record_user_event(
                    user_id, "email_changed", old_email, new_email, reason
                )
            if new_password is not None:
                self.profiles.record_user_event(user_id, "password_reset", None, None, reason)
            self.profiles.commit()
        except BaseException:
            self.profiles.session.rollback()
            if admin is not None and new_email is not None and old_email:
                # Only matters if the Auth change succeeded and the commit then failed: restore the email.
                try:
                    admin.update_user(
                        user_id,
                        email=old_email,
                        email_confirmed=self.settings.provision_email_confirmed,
                    )
                except AppError:
                    logger.error(
                        "could not restore the email of user %s after a failed edit", user_id
                    )
            raise
        refreshed = self.profiles.get(user_id)
        assert refreshed is not None
        return refreshed

    @staticmethod
    def _compensate(admin: SupabaseAuthAdmin, user_id: uuid.UUID) -> None:
        try:
            admin.delete_user(user_id)
        except AppError:
            logger.error("could not remove Auth user %s after a failed profile insert", user_id)


def change_own_password(
    profiles: ProfileRepository,
    settings: Settings,
    user_id: uuid.UUID,
    new_password: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Set the caller's new password in Supabase Auth, then clear their must-change flag.

    If clearing the flag fails the password is already changed and the call can simply be repeated."""
    build_auth_admin(settings, transport).update_password(user_id, new_password)
    profiles.complete_password_change()

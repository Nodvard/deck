"""Alle Kern-Modelle; der Import hier macht sie für Alembic sichtbar.

Tabellen von Erweiterungen liegen bei der Erweiterung (Präfix `ext_<id>_`, eigener Alembic-Zweig)."""

from __future__ import annotations

from ..db.base import Base
from .identity import ApiToken, RecoveryCode, RefreshToken, Role, RolePermission, User, user_roles
from .infra import (
    Host,
    HostCredential,
    HostGroup,
    HostTag,
    KnownHostKey,
    host_group_members,
)
from .platform import (
    ConnectorInstance,
    CustomApp,
    DashboardLayout,
    ExtensionRecord,
    Job,
    JobRun,
    Notification,
    NotificationDelivery,
    Setting,
)
from .security import Action, ActionFlapHistory, AuditEntry, Secret, SecretGrant, SecretUsage

__all__ = [
    "Base",
    "User", "Role", "RolePermission", "RefreshToken", "RecoveryCode", "ApiToken", "user_roles",
    "Host", "HostTag", "HostGroup", "HostCredential", "KnownHostKey", "host_group_members",
    "Secret", "SecretGrant", "SecretUsage", "AuditEntry", "Action", "ActionFlapHistory",
    "ExtensionRecord", "ConnectorInstance", "CustomApp", "Job", "JobRun",
    "Notification", "NotificationDelivery", "Setting", "DashboardLayout",
]

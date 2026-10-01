"""Alle Kern-Modelle. Import hier sorgt dafuer, dass Alembic sie sieht.

Extension-Tabellen stehen NICHT hier — sie liegen bei der jeweiligen Extension, tragen
zwingend den Praefix `ext_<id>_` und haben einen eigenen Alembic-Branch.
"""

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
from .security import Action, ActionFlapHistory, AuditEntry, Secret, SecretGrant

__all__ = [
    "Base",
    "User", "Role", "RolePermission", "RefreshToken", "RecoveryCode", "ApiToken", "user_roles",
    "Host", "HostTag", "HostGroup", "HostCredential", "KnownHostKey", "host_group_members",
    "Secret", "SecretGrant", "AuditEntry", "Action", "ActionFlapHistory",
    "ExtensionRecord", "ConnectorInstance", "CustomApp", "Job", "JobRun",
    "Notification", "NotificationDelivery", "Setting", "DashboardLayout",
]

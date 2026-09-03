"""#t63 内置角色、权限与菜单常量。"""

from __future__ import annotations

from typing import Final

BUILTIN_SYSTEM_ADMIN = "system_admin"
BUILTIN_ORG_ADMIN = "org_admin"
BUILTIN_AUDITOR = "auditor"
BUILTIN_USER = "user"

ALL_MENU_KEYS: Final[tuple[str, ...]] = (
    "dashboard",
    "assets",
    "accounts",
    "sessions",
    "audits",
    "workflows",
    "tenancy",
    "rbac",
    "settings",
)

ADMIN_CONSOLE_PERMISSIONS: Final[tuple[str, ...]] = (
    "admin",
    "assets:read",
    "assets:write",
    "assets:test",
    "audit:read",
    "audit:write",
    "sessions:connect",
    "workflow:approve",
    "workflow:audit",
    "workflow:admin",
)

MVP_CONSOLE_PERMISSIONS: Final[tuple[str, ...]] = ("assets:read", "sessions:connect")

BUILTIN_ROLE_DEFINITIONS: Final[dict[str, dict[str, object]]] = {
    BUILTIN_SYSTEM_ADMIN: {
        "name": "system_admin",
        "display_name": "系统管理员",
        "scope_type": "system",
        "organization_id": None,
        "description": "租户级系统管理员，拥有全部管理权限。",
        "permissions": (
            *ADMIN_CONSOLE_PERMISSIONS,
            "rbac:read",
            "rbac:manage",
            "tenancy:read",
            "tenancy:write",
            "accounts:read",
            "accounts:write",
            "accounts:automate",
            "automation:read",
            "automation:write",
        ),
        "menus": ALL_MENU_KEYS,
    },
    BUILTIN_ORG_ADMIN: {
        "name": "org_admin",
        "display_name": "组织管理员",
        "scope_type": "organization",
        "organization_id": None,
        "description": "组织范围管理员，可管理本组织资产、账号与工单。",
        "permissions": (
            "assets:read",
            "assets:write",
            "accounts:read",
            "accounts:write",
            "accounts:automate",
            "automation:read",
            "automation:write",
            "audit:read",
            "sessions:connect",
            "workflow:approve",
            "workflow:admin",
            "tenancy:read",
            "rbac:read",
        ),
        "menus": (
            "dashboard",
            "assets",
            "accounts",
            "sessions",
            "audits",
            "workflows",
            "tenancy",
            "rbac",
        ),
    },
    BUILTIN_AUDITOR: {
        "name": "auditor",
        "display_name": "审计员",
        "scope_type": "system",
        "organization_id": None,
        "description": "只读审计与工单审计权限。",
        "permissions": ("audit:read", "workflow:audit", "sessions:connect"),
        "menus": ("dashboard", "audits", "sessions"),
    },
    BUILTIN_USER: {
        "name": "user",
        "display_name": "普通用户",
        "scope_type": "system",
        "organization_id": None,
        "description": "默认业务用户，可查看资产并发起会话。",
        "permissions": MVP_CONSOLE_PERMISSIONS,
        "menus": ("dashboard", "assets", "sessions"),
    },
}

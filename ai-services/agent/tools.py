"""Whitelisted Safe AI Tools Definitions & Security Rules (Rules 60, 61, 62, 65).

Invariants:
- Rule 60: The LLM model NEVER writes SQL. Only safe whitelisted Python tool functions are invoked.
- Rule 61: Tool calls run with the caller's explicit permissions (RBAC).
- Rule 62: Any mutating action creates a DRAFT requiring human approval.
- Rule 65: Every tool execution is logged with user, parameters, duration, and status.
"""

from dataclasses import dataclass
from typing import Any

# Authorized roles per tool
TOOL_PERMISSIONS: dict[str, list[str]] = {
    "check_stock": ["owner", "factory", "warehouse", "distributor", "dealer"],
    "get_sales_summary": ["owner"],
    "get_slow_movers": ["owner"],
    "get_dealer_history": ["owner", "dealer", "distributor"],
    "create_order_draft": ["owner", "dealer", "distributor"],
}

# Catalog of tool descriptions exposed to the LLM agent prompt
TOOL_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "check_stock": {
        "description": "Check current available stock for product variants across warehouses.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "description": "Optional SKU to filter, e.g. 'GM-250G'",
                }
            },
        },
    },
    "get_sales_summary": {
        "description": "Owner only: Aggregated sales summary across recent order cycles.",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["last_7_days", "last_30_days", "last_90_days", "all_time"],
                },
                "group_by": {
                    "type": "string",
                    "enum": ["variant", "product", "category"],
                },
            },
        },
    },
    "get_slow_movers": {
        "description": "Owner only: Identify batches with high holding time and low sales velocity.",
        "parameters": {
            "type": "object",
            "properties": {
                "threshold_days": {
                    "type": "integer",
                    "description": "Days since production without sales (default: 45)",
                }
            },
        },
    },
    "get_dealer_history": {
        "description": "Retrieve past order history. Dealers can only access their own history.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent orders to fetch",
                }
            },
        },
    },
    "create_order_draft": {
        "description": "Prepare a suggested reorder draft. Produces a DRAFT requiring human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Optional notes or reason for order",
                }
            },
        },
    },
}


@dataclass
class ToolRunResult:
    tool_name: str
    status: str  # "SUCCESS", "FORBIDDEN", "FAILED"
    data: Any
    error_message: str | None = None


def check_tool_permission(tool_name: str, user_role: str) -> bool:
    """Verify if user_role is authorized to execute tool_name (Rule 61)."""
    allowed_roles = TOOL_PERMISSIONS.get(tool_name, [])
    return user_role.lower() in allowed_roles

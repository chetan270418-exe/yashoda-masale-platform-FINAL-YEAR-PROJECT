"""Tests for Whitelisted Tools, RBAC, and Audit Logging (Rules 60, 61, 62, 65)."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIToolRun, OrderDraft
from app.ai.tools import (
    ToolArgumentError,
    ToolPermissionError,
    execute_tool,
)
from app.auth.models import User
from tests.conftest import _make_user


@pytest.fixture
def owner_user(db: Session) -> User:
    return _make_user(db, email=f"owner-{uuid.uuid4().hex[:6]}@example.com", role_name="owner")


@pytest.fixture
def dealer_user(db: Session) -> User:
    return _make_user(db, email=f"dealer-{uuid.uuid4().hex[:6]}@example.com", role_name="dealer")


@pytest.fixture
def consumer_user(db: Session) -> User:
    return _make_user(
        db, email=f"consumer-{uuid.uuid4().hex[:6]}@example.com", role_name="consumer"
    )


def test_check_stock_tool_accessible_by_all_and_logged(db: Session, consumer_user: User):
    """check_stock is public/staff readable and creates an AIToolRun log entry (Rule 65)."""
    result = execute_tool(db, consumer_user, "check_stock", {})
    assert "stock" in result

    # Check ai_tool_runs entry
    log = db.scalar(
        select(AIToolRun)
        .where(AIToolRun.user_id == consumer_user.id, AIToolRun.tool == "check_stock")
        .order_by(AIToolRun.created_at.desc())
    )
    assert log is not None
    assert log.status == "success"


def test_sales_summary_owner_only_rbac(db: Session, owner_user: User, dealer_user: User):
    """get_sales_summary is permitted for owner, denied for dealer (Rule 61)."""
    # Owner succeeds
    owner_result = execute_tool(db, owner_user, "get_sales_summary", {"period": "last_30_days"})
    assert "sales" in owner_result

    # Dealer fails
    with pytest.raises(ToolPermissionError):
        execute_tool(db, dealer_user, "get_sales_summary", {"period": "last_30_days"})

    # Denied attempt is logged in ai_tool_runs
    denied_log = db.scalar(
        select(AIToolRun)
        .where(AIToolRun.user_id == dealer_user.id, AIToolRun.tool == "get_sales_summary")
        .order_by(AIToolRun.created_at.desc())
    )
    assert denied_log is not None
    assert denied_log.status == "denied"


def test_slow_movers_owner_only_rbac(db: Session, owner_user: User, consumer_user: User):
    """get_slow_movers is restricted to owner."""
    owner_res = execute_tool(db, owner_user, "get_slow_movers", {"threshold_days": 60})
    assert "slow_movers" in owner_res

    with pytest.raises(ToolPermissionError):
        execute_tool(db, consumer_user, "get_slow_movers", {"threshold_days": 60})


def test_dealer_history_isolation(db: Session, owner_user: User, dealer_user: User):
    """Dealers may only view their own history; Owners can view any (Rule 61)."""
    # Dealer queries own
    dealer_res = execute_tool(db, dealer_user, "get_dealer_history", {"dealer_id": dealer_user.id})
    assert dealer_res["dealer_id"] == str(dealer_user.id)

    # Dealer tries to query owner's ID -> rejected
    with pytest.raises(ToolPermissionError):
        execute_tool(db, dealer_user, "get_dealer_history", {"dealer_id": owner_user.id})


def test_create_order_draft_creates_draft_requiring_human_approval(db: Session, dealer_user: User):
    """create_order_draft produces DRAFT status pending human confirmation (Rule 62)."""
    res = execute_tool(db, dealer_user, "create_order_draft", {"notes": "Monthly order"})
    assert res["status"] == "draft"
    assert res["requires_human_approval"] is True

    draft_id = uuid.UUID(res["draft_id"])
    draft_record = db.scalar(select(OrderDraft).where(OrderDraft.id == draft_id))
    assert draft_record is not None
    assert draft_record.status == "draft"
    assert draft_record.customer_id == dealer_user.id


def test_unknown_tool_attempt_rejected_and_logged_denied(db: Session, owner_user: User):
    """Invoking an unwhitelisted tool (e.g. SQL injection attempt) is rejected (Rule 60)."""
    with pytest.raises(ToolArgumentError):
        execute_tool(db, owner_user, "execute_sql_query", {"query": "SELECT * FROM users"})

    log = db.scalar(
        select(AIToolRun)
        .where(AIToolRun.tool == "execute_sql_query")
        .order_by(AIToolRun.created_at.desc())
    )
    assert log is not None
    assert log.status == "denied"

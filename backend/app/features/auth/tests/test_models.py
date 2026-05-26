"""Structural tests for the auth ORM models (no DB required)."""

from sqlalchemy import CheckConstraint, ForeignKey, Table
from sqlalchemy.orm import RelationshipProperty

from app.features.auth.models import Session, User


def test_user_table_metadata() -> None:
    assert User.__tablename__ == "users"
    table: Table = User.__table__  # type: ignore[assignment]
    col_names = set(table.columns.keys())
    expected = {
        "id",
        "username",
        "password_hash",
        "role",
        "terms_accepted_at",
        "created_at",
        "updated_at",
    }
    assert expected.issubset(col_names)

    # role column has a CHECK constraint
    check_constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    assert any("role" in str(c.sqltext) for c in check_constraints)

    # username column is unique
    assert table.columns["username"].unique is True


def test_session_table_metadata() -> None:
    assert Session.__tablename__ == "sessions"
    table: Table = Session.__table__  # type: ignore[assignment]
    col_names = set(table.columns.keys())
    assert {
        "id",
        "user_id",
        "created_at",
        "last_used_at",
        "expires_at",
        "user_agent",
        "ip",
    }.issubset(col_names)

    # user_id has a FK to users.id with ON DELETE CASCADE
    user_id_col = table.columns["user_id"]
    fks = list(user_id_col.foreign_keys)
    assert len(fks) == 1
    fk: ForeignKey = next(iter(fks))
    assert fk.column.table.name == "users"
    assert fk.column.name == "id"
    assert fk.ondelete == "CASCADE"


def test_session_indexes_present() -> None:
    table: Table = Session.__table__  # type: ignore[assignment]
    index_names = {idx.name for idx in table.indexes}
    assert "ix_sessions_user_id" in index_names
    assert "ix_sessions_expires_at" in index_names


def test_user_sessions_relationship() -> None:
    sessions_attr = User.sessions
    assert isinstance(sessions_attr.property, RelationshipProperty)

    user_attr = Session.user
    assert isinstance(user_attr.property, RelationshipProperty)

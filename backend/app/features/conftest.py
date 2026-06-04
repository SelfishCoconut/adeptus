"""Shared SQLite-compat patch for all feature tests.

Since Slice 10, login/logout/tool-run/graph-edit emit hash-chained audit entries, so
any feature test that authenticates or performs an audited action inserts into
``audit_entries``. Patch ``AuditEntry.id``'s Postgres ``gen_random_uuid()`` server
default to a Python-side ``uuid4`` ONCE here (at conftest import, before any
``create_all``) so every feature's in-memory SQLite test DB can insert audit rows —
instead of repeating the patch in each feature's conftest. Production never imports
conftest, so the real ``gen_random_uuid()`` default is untouched there.
"""

from uuid import uuid4

from sqlalchemy import Column, ColumnDefault

from app.features.audit import models as _audit_models

_audit_id_col: Column = _audit_models.AuditEntry.__table__.c.id  # type: ignore[assignment]
_audit_id_col.default = ColumnDefault(uuid4)

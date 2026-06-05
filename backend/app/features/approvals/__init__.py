"""Two-tier autonomy + approval flow (Slice 16).

Classifies AI-proposed tool commands into ``autonomous`` (run immediately) vs
``requires_approval`` (gate behind a human approve/reject), records the engagement-
shared ``approval_requests`` queue, and emits the hash-chained ``approval_granted`` /
``approval_rejected`` audit entries with the ``self_approved`` attribution (§5.2 / §14).
"""

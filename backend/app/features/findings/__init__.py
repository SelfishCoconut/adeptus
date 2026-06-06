"""Findings feature (Slice 19).

A finding is a human-authored vulnerability record with a Simple severity
(§9.1) and a two-axis lifecycle — verification (§9.2) and remediation (§9.2).
It optionally links to a GraphNode by FK (§8.1) but is NOT a graph entity and
does NOT route through the single-writer process (Decision 1 / ADR-0001).
"""

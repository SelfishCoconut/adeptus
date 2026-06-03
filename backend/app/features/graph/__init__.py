"""Graph feature: per-engagement knowledge graph (nodes + edges) owned by a
single-writer process per engagement (ADR-0001), persisted to PostgreSQL and
mirrored to an in-memory NetworkX graph for traversal/reads."""

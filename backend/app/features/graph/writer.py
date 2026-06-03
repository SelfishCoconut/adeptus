"""The single-writer process (ADR-0001): one writer per engagement owning the
in-memory NetworkX graph and serializing every write through a single consumer
task. The critical invariant of this slice (task 4)."""

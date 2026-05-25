# 0004. Default local LLM is qwen3.5:9b


Date: 2026-05-24
Status: Accepted

## Context

Requirements §5.1 specifies "Ollama with a small quantized model" as the default for local-first operation. The model needs reasonable tool-use quality (function calling, structured output) while running on pentester laptops without requiring a workstation-class GPU.

## Decision

The default model pinned in `docker-compose.yml` is `qwen3.5:9b
`. The model is configurable via the `ADEPTUS_LLM_MODEL` environment variable; the default is documented in the deployment runbook.

## Consequences

**Positive**
- ~5 GB VRAM footprint — runs on M-series Macs, RTX 3060+, or CPU fallback
- Good tool-use quality at this size in 2026 benchmarks
- Apache-2.0 licensed model — no compliance surprises
- Streaming and function calling both work in Ollama

**Negative**
- Quality is below frontier cloud models — the cloud opt-in via the per-engagement toggle exists precisely for cases where local quality is insufficient
- First model pull is ~5 GB over the network

**Neutral**
- Swappable per deployment; nothing in the code is model-specific

## Alternatives considered

- **llama3.2:3b**: smaller and faster but materially worse at tool use, which dominates Adeptus's workload.
- **qwen2.5:14b**: better quality but doubles VRAM requirements, putting it out of reach for many pentester laptops.
- **mistral-nemo:12b**: comparable quality, larger footprint, weaker tool-call adherence in our spot checks.

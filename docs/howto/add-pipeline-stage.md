# How to: extend the extraction pipeline

The pipeline lives in `app/services/` as thick subpackages
(`ingest`, `segment`, `extract`, `resolve`) wired together by
`app/services/pipeline.py`. Each stage is swappable behind a Protocol.

## Add a new extractor

Extractors turn a `Segment` into grounded `ExtractedClaim`s. The contract is
`app/services/extract/base.py`:

```python
class Extractor(Protocol):
    def extract(self, segment: Segment) -> list[ExtractedClaim]: ...
```

Every `ExtractedClaim` MUST carry `source_utterance_ids` with ≥1 id — the
grounding rule. Output without sources is rejected at the schema layer and
again by `insert_claim_with_sources`.

1. Add `app/services/extract/my_extractor.py` implementing `extract`.
2. Export it from `app/services/extract/__init__.py`.
3. Pass an instance to `run_text_pipeline(..., extractor=MyExtractor())`.

`RuleExtractor` (deterministic, no LLM) is the default and the test baseline;
`LLMExtractor` is the production path (Instructor/vLLM via the LLM settings
in `app/core/config.py`).

## Add a new embedder

Embedders implement `app/services/resolve/embedder.py`'s `Embedder`:

```python
class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...   # EMBEDDING_DIM, L2-normalized
```

`DeterministicEmbedder` keeps CI green without ML deps; `BGEEmbedder` is the
production path (`uv sync --extra ml`). The background task
`app/worker/background/pipeline.py` takes an `embedder` argument.

## Run the whole path

`app/services/pipeline.py::run_text_pipeline` does ingest → segment →
extract → store in one session (caller owns the commit). The background
variant `worker/background/pipeline.py::ingest_and_embed` adds the embedding
step and manages its own `session_scope`.

## Invariants to preserve

- Offsets are Unicode codepoints; never lose them in segmentation
  (`tests/test_offsets.py`).
- No orphan claims — every claim has ≥1 `claim_sources` row
  (`tests/test_grounded_pipeline.py`).
- Substrate (`utterances`) and provenance (`claim_sources`) are append-only.

# Multi-artifact embedding and novelty alignment (TODO-001 / TODO-102)

This ExecPlan is a living document and must be maintained in full compliance with PLANS.md (repo root: PLANS.md).

## Purpose / Big Picture

Embeddings and novelty checks currently look at a single primary file, so agentic edits in supporting files—or any non-code artifacts—are invisible. After this change, each generation will build an artifact-agnostic “corpus” that represents all relevant files (text or binary placeholders) within configured limits. Embeddings, similarity, and novelty rejection sampling will use that corpus, giving correct behavior for any evolveable project: multi-file codebases, docs, assets, or other digital artifacts.

## Progress

- [x] (2025-11-27 23:10Z) Drafted ExecPlan v1 (this document) for TODO-001/TODO-102
- [x] (2025-11-27 23:40Z) Implemented artifact-agnostic corpus builder, config fields, runner integration, and novelty alignment
- [x] (2025-11-27 23:42Z) Added regression/unit tests for corpus handling and novelty LLM text flow
- [x] (2025-11-27 23:50Z) Ran targeted tests via `uv run python -m pytest tests/test_embedding_corpus.py` and `uv run python -m pytest tests -k embedding --maxfail=1`
- [x] (2025-11-28 12:55Z) Validated `EvolutionRunner` integration with `validate_embedding_integration.py`, fixing a bug where `redact_immutable` was wiping out the corpus text.

## Surprises & Discoveries

- The bare `pytest` executable is not on PATH; running via `uv run python -m pytest ...` works and was used for validation.
- **Critical Bug Found:** `redact_immutable` (legacy feature) was stripping all content from the new corpus format because it lacked `EVOLVE-BLOCK` markers. Fixed by conditionally skipping redaction in `runner.py` when `=== FILE:` header is detected.

## Decision Log

- Decision: Scope is artifact-agnostic (no extension- or language-specific assumptions); binary assets receive hashed placeholders instead of raw bytes. Rationale: Users want to evolve arbitrary digital artifacts, not just code. Date/Author: 2025-11-27 / assistant.
- Decision: Skip `redact_immutable` for multi-file corpora. Rationale: Corpus represents the full state of the generation; determining "immutable" regions in a mixed bag of files (images, text, code) is not feasible or desirable for high-level embeddings.

## Outcomes & Retrospective

- **TODO-001 Verified:** The system now correctly aggregates all files, generates embeddings from the full corpus, and preserves this data for novelty judgment. The integration bug in `runner.py` is fixed.

## Context and Orientation

Embedding computation lives in `shinka/core/runner.py` (`get_code_embedding`), which reads only the exec file. `Program.code` in `shinka/database/dbase.py` stores that single file; novelty (`NoveltyJudge.assess_novelty_with_rejection_sampling`) also reads the same path for LLM comparison. Agentic runs already track multi-file changes (`agent_changed_files`, `agent_binary_files`) and copy whole workspaces, but embeddings/similarity/clusters ignore everything except the primary file. Goal: build a canonical per-generation corpus covering all relevant artifacts, within size limits, and feed it consistently to embeddings, similarity, novelty, and stored `Program.code`, without any code-only assumptions.

## Plan of Work

1) Build an artifact-agnostic corpus helper (new module under `shinka/core/`) that walks the generation directory (or a prioritized changed-files list), skips existing excluded dirs (`results`, `workspace_snapshot`, `agent_sessions`, `.hydra`, `__pycache__`), honors new include/exclude globs, and deterministically orders by relative path. It should detect textual vs binary by decoding heuristics; include textual content up to per-file and total byte caps (with path/size headers); record binary or over-limit files as placeholders containing path, size, and a short hash. Return a dataclass (e.g., `EmbeddingCorpus`) with corpus text plus metadata about included/skipped files and truncation.

2) Wire corpus into embedding flow: update `EvolutionRunner.get_code_embedding` (or a wrapper) to accept corpus text instead of reading a single file; store the corpus text in `Program.code` and attach corpus metadata (files included, skipped counts, truncation flags) into `Program.metadata`.

3) Align novelty with the corpus: pass corpus text into `NoveltyJudge.assess_novelty_with_rejection_sampling` and `check_llm_novelty` so similarity and LLM comparisons use the same corpus representation. If corpus is empty, log and skip novelty gracefully.

4) Config: add embedding options to `EvolutionConfig` (e.g., `embedding_include_globs`, `embedding_exclude_globs`, `embedding_max_files`, `embedding_max_total_bytes`, `embedding_max_bytes_per_file`, `embedding_use_changed_files_first`) with safe deterministic defaults that cover all files but cap size.

5) DB/schema: keep existing schema; repurpose `Program.code` to store corpus text. Document this change in code comments; no migration required.

6) Tests: 
   - Corpus helper unit tests with mixed text/binary/large files and deterministic ordering/truncation.
   - Regression test where only a non-primary artifact changes; assert embeddings differ and novelty sees the change (using stubbed embedding client to avoid network).
   - Smoke run of a tiny evolution step to ensure defaults work unchanged.

7) Validation and docs: run the new tests plus a focused subset (`pytest tests -k embedding`); capture key log snippets showing corpus construction and novelty acceptance. Update inline comments to explain corpus behavior and limits.

## Concrete Steps

- Add corpus builder module and dataclass; thread new config fields through `EvolutionConfig`.
- Replace single-file reads in embedding and novelty paths with corpus text and metadata.
- Write unit tests for corpus builder and helper-only change regression; run `pytest tests/test_embedding_corpus.py`, `pytest tests/test_novelty_multifile.py`, and `pytest tests -k embedding --maxfail=1`.
- Optional: run a short local evolution to confirm no runtime regressions.

## Success Criteria & Validation

- Corpus builder tests pass, showing deterministic ordering, placeholder handling for binary/oversized files, and enforced limits.
- Regression test proves embeddings change when a non-primary artifact changes and novelty rejection sampling respects that change.
- Targeted test subset (`pytest tests -k embedding`) passes.
- Manual sanity (optional): short evolution run completes with corpus metadata stored in DB and no embedding/novelty errors.

## Idempotence and Recovery

Corpus building is pure and deterministic given the same directory contents and config. Size caps prevent unbounded memory use. If corpus is empty or embedding fails, runner should log and skip novelty instead of crashing. Re-running tests or sample evolutions is safe; no schema migrations are introduced.

## Artifacts and Notes

Capture concise evidence after each validation step: test pass summaries and any log lines demonstrating corpus inclusion or novelty acceptance/skip.

## Interfaces and Dependencies

- New helper (e.g., `shinka/core/embedding_corpus.py`) exporting `build_embedding_corpus(root: Path, config: EmbeddingConfig, changed_first: List[Path] | None) -> EmbeddingCorpus`.
- Extend `EvolutionConfig` with embedding include/exclude globs and size limits; defaults deterministic and artifact-agnostic.
- `EvolutionRunner.get_code_embedding` to accept corpus text (and maybe corpus meta) and return `(embedding: List[float], cost: float)` using existing `EmbeddingClient`.
- `NoveltyJudge` to consume corpus text for both similarity and LLM novelty checks.
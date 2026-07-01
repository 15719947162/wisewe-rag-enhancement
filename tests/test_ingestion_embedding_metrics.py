from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import ingestion_service
from core.embedding.client import EmbeddingRun
from core.models.content_block import BlockType, Chunk, ContentBlock


class _FakeConn:
    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


@pytest.mark.asyncio
async def test_confirm_pipeline_records_embedding_metrics() -> None:
    task = {
        "id": "task-1",
        "kb_id": "kb-1",
        "filename": "demo.pdf",
        "strategy": "hierarchical",
        "status": "awaiting_confirmation",
        "stages": {key: ingestion_service._new_stage_state() for key in ingestion_service.STAGE_KEYS},
        "awaiting_confirmation": True,
        "created_at": "",
        "updated_at": "",
        "blocks_preview": [],
        "chunks_preview": [],
        "removed_reasons": [],
        "quality_breakdown": [],
    }
    chunks = [Chunk(content="hello", source="demo.pdf", page=1, chunk_index=0)]
    embedding_run = EmbeddingRun(
        embeddings=[[0.1, 0.2]],
        metrics={
            "batchSize": 10,
            "batchCount": 1,
            "maxConcurrency": 4,
            "retryCount": 0,
            "embeddingWallMs": 12,
        },
    )

    def _fake_load_task(_task_id):
        return task

    with patch("backend.services.ingestion_service.load_task", side_effect=_fake_load_task), patch(
        "backend.services.ingestion_service.save_task"
    ), patch(
        "backend.services.ingestion_service.load_confirmable_chunks",
        return_value=chunks,
    ), patch(
        "backend.services.ingestion_service.clear_chunk_drafts",
    ), patch(
        "core.cleaner.quality_gate.apply_quality_gate",
        return_value=SimpleNamespace(chunks=chunks, discarded_count={}),
    ), patch(
        "core.embedding.client.embed_texts_with_metrics",
        return_value=embedding_run,
    ), patch(
        "core.chunker.semantic_linker.link_semantic",
    ), patch(
        "core.chunker.procedure_linker.detect_procedure_chunks",
    ), patch(
        "core.chunker.procedure_linker.link_procedure",
    ), patch(
        "core.chunker.causal_linker.link_causal",
    ), patch(
        "core.db.connection.get_db_connection",
        return_value=_FakeConn(),
    ), patch(
        "core.kg.extraction_pipeline.materialize_entities",
    ), patch(
        "core.output.pgvector_writer.write_to_pgvector",
        return_value={"written": 1, "document_id": "doc-1", "kb_id": "kb-1", "skipped": False},
    ):
        payload = await ingestion_service.confirm_pipeline("task-1")

    embedding_stage = next(stage for stage in payload["stages"] if stage["key"] == "embedding")
    assert embedding_stage["metrics"]["batchSize"] == 10
    assert embedding_stage["metrics"]["maxConcurrency"] == 4
    assert embedding_stage["metrics"]["embeddingWallMs"] == 12
    assert "linkSemanticMs" in embedding_stage["metrics"]
    assert "linkProcedureMs" in embedding_stage["metrics"]
    assert "linkCausalMs" in embedding_stage["metrics"]
    export_stage = next(stage for stage in payload["stages"] if stage["key"] == "export")
    assert "entityMaterializeMs" in export_stage["metrics"]
    assert "pgvectorWriteMs" in export_stage["metrics"]


@pytest.mark.asyncio
async def test_run_pipeline_basic_ready_skips_hierarchical_enhancement(tmp_path) -> None:
    source = tmp_path / "demo.pdf"
    source.write_bytes(b"%PDF-1.4")
    task = {
        "id": "task-basic",
        "kb_id": "kb-1",
        "filename": "demo.pdf",
        "strategy": "hierarchical",
        "subject_type": "general",
        "layout_type": "single_column",
        "source_path": str(source),
        "file_bytes": None,
        "status": "pending",
        "current_stage": None,
        "stages": {key: ingestion_service._new_stage_state() for key in ingestion_service.STAGE_KEYS},
        "awaiting_confirmation": False,
        "chunk_count": 0,
        "blocks_preview": [],
        "chunks_preview": [],
        "removed_reasons": [],
        "quality_breakdown": [],
        "created_at": "",
        "updated_at": "",
    }
    blocks = [ContentBlock(type=BlockType.TEXT, text="hello", page_idx=0, source_file="demo.pdf")]
    chunks = [Chunk(content="hello", source="demo.pdf", page=0, chunk_index=0, layer="child")]
    captured_kwargs: dict = {}

    class FakeStrategy:
        last_timings = {"chunkBaseMs": 1, "enhanceTasks": 0}

        def chunk(self, _blocks):
            return chunks

    def fake_get_strategy(_name: str, **kwargs):
        captured_kwargs.update(kwargs)
        return FakeStrategy()

    logger = SimpleNamespace(info=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None)
    finalize = AsyncMock(return_value={"ok": True})

    with patch("backend.services.ingestion_service.get_task", return_value=task), patch(
        "backend.services.ingestion_service.save_task"
    ), patch("core.logging.get_task_logger", return_value=logger), patch("core.logging.close_task_logger"), patch(
        "core.runtime_settings.resolve_runtime_setting",
        return_value=("basic", "env"),
    ), patch(
        "core.parser.provider.get_pdf_parser_provider",
        return_value="stub",
    ), patch(
        "core.parser.provider.parse_pdf",
        return_value=blocks,
    ), patch(
        "core.cleaner.clean_blocks",
        return_value=SimpleNamespace(blocks=blocks, removed_blocks=[], metrics={}),
    ), patch(
        "core.chunker.get_strategy",
        side_effect=fake_get_strategy,
    ), patch(
        "backend.services.ingestion_service.link_related_chunks",
        return_value=chunks,
    ), patch(
        "backend.services.ingestion_service.save_chunk_drafts",
    ), patch(
        "backend.services.ingestion_service._finalize_pipeline",
        finalize,
    ):
        await ingestion_service.run_pipeline_real("task-basic")

    assert captured_kwargs["enable_enhanced"] is False
    assert task["ingestion_ready_mode"] == "basic"
    assert task["ingestion_ready_mode_requested"] == "basic"
    assert task["chunk_timings"]["readyMode"] == "basic"
    assert task["chunk_timings"]["enhancementSkipped"] == 1
    finalize.assert_awaited_once()

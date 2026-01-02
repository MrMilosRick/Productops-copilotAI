import hashlib
from celery import shared_task
from django.db import transaction

from copilot.models import Document, EmbeddingChunk
from copilot.services.chunking import chunk_text


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@shared_task
def chunk_document(document_id: int) -> dict:
    doc = Document.objects.get(id=document_id)

    chunks = chunk_text(doc.content or "", max_chars=3500, overlap_chars=300)

    objs = [
        EmbeddingChunk(
            document_id=doc.id,
            chunk_index=c["chunk_index"],
            text=c["text"],
            meta=c.get("meta", {}),
        )
        for c in chunks
    ]

    with transaction.atomic():
        # clean rebuild (idempotent under our process_document lock)
        EmbeddingChunk.objects.filter(document_id=doc.id).delete()

        if objs:
            EmbeddingChunk.objects.bulk_create(objs)

        doc.status = "chunked"
        doc.chunk_count = len(chunks)
        doc.content_hash = sha256_text(doc.content or "")
        doc.save(update_fields=["status", "chunk_count", "content_hash"])

    return {"document_id": doc.id, "chunks": len(chunks)}


@shared_task
def process_document(document_id: int) -> dict:
    # DB-level idempotency lock via atomic status update.
    # If another worker is already processing (status=chunking), skip.
    updated = (
        Document.objects.filter(id=document_id)
        .exclude(status="chunking")
        .update(status="chunking")
    )
    if updated == 0:
        return {"document_id": int(document_id), "status": "skipped_locked"}

    try:
        return chunk_document(document_id)
    except Exception:
        Document.objects.filter(id=document_id).update(status="error")
        raise

import hashlib

from celery import shared_task
from django.db import transaction

from copilot.models import Document, EmbeddingChunk
from copilot.services.chunking import chunk_text


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def process_document(self, document_id: int) -> dict:
    # DB-level lock: mark as chunking once. If someone else already chunking/chunked -> skip.
    updated = (
        Document.objects
        .filter(id=document_id)
        .exclude(status__in=["chunking", "chunked"])
        .update(status="chunking")
    )
    if updated == 0:
        doc = Document.objects.filter(id=document_id).first()
        return {
            "document_id": int(document_id),
            "status": getattr(doc, "status", "missing"),
            "skipped": True,
        }

    doc = Document.objects.get(id=document_id)

    try:
        chunks = chunk_text(doc.content or "", max_chars=3500, overlap_chars=300)

        with transaction.atomic():
            # Rebuild chunks deterministically
            EmbeddingChunk.objects.filter(document=doc).delete()

            objs = [
                EmbeddingChunk(
                    document=doc,
                    chunk_index=i,
                    text=c["text"],
                    meta=c.get("meta", {}),
                )
                for i, c in enumerate(chunks)
            ]
            if objs:
                EmbeddingChunk.objects.bulk_create(objs)

            doc.status = "chunked"
            doc.chunk_count = len(chunks)
            doc.content_hash = sha256_text(doc.content or "")
            doc.save(update_fields=["status", "chunk_count", "content_hash"])

        return {"document_id": doc.id, "status": doc.status, "chunks": doc.chunk_count}

    except Exception:
        Document.objects.filter(id=document_id).update(status="error")
        raise

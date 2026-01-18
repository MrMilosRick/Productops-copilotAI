import hashlib

from celery import shared_task
from django.db import transaction

from copilot.models import Document, EmbeddingChunk
from copilot.services.chunking import chunk_text
from copilot.services.embeddings import embed_texts


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def process_document(self, document_id: int) -> dict:
    # DB-level lock: mark as chunking once. If someone else already chunking/chunked -> skip.
    updated = (
        Document.objects
        .filter(id=document_id)
        .exclude(status__in=["chunking"])
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

                # Compute + persist embeddings (stub for now)
                chunks_qs = EmbeddingChunk.objects.filter(document=doc).order_by("chunk_index")
                texts = [c.text for c in chunks_qs]
                vectors = embed_texts(texts) if texts else []
                for c, v in zip(chunks_qs, vectors):
                    c.embedding = v
                    c.save(update_fields=["embedding"])

            doc.status = "embedded"
            doc.chunk_count = len(chunks)
            doc.content_hash = sha256_text(doc.content or "")
            doc.save(update_fields=["status", "chunk_count", "content_hash"])

        return {"document_id": doc.id, "status": doc.status, "chunks": doc.chunk_count}

    except Exception:
        Document.objects.filter(id=document_id).update(status="failed")
        raise

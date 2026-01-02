from celery import shared_task
from django.db import transaction

from copilot.models import Document, EmbeddingChunk


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        j = min(i + chunk_size, n)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        if j >= n:
            break
        i = max(0, j - overlap)
    return chunks


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def process_document(self, document_id: int) -> dict:
    doc = Document.objects.get(id=document_id)

    # идемпотентность таска
    if getattr(doc, "status", "") in ("chunked", "ready"):
        return {"status": doc.status, "chunk_count": getattr(doc, "chunk_count", Non= "chunking"
    doc.save(update_fields=["status"])

    chunks = split_text(getattr(doc, "content", "") or "")

    with transaction.atomic():
        EmbeddingChunk.objects.filter(document=doc).delete()

        objs = [
            EmbeddingChunk(
                document=doc,
                chunk_index=idx,
                text=chunk,  # <-- если поле не text — заменим ниже командой
            )
            for idx, chunk in enumerate(chunks)
        ]
        if objs:
            EmbeddingChunk.objects.bulk_create(objs)

        doc.chunk_count = len(chunks)
        doc.status = "chunked"
        doc.save(update_fields=["chunk_count", "status"])

    return {"status": doc.status, "chunk_count": doc.chunk_count}

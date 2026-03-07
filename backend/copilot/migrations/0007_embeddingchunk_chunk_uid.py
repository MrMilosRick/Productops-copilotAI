# Add EmbeddingChunk.chunk_uid (stable deterministic ID) + backfill
import hashlib
from django.db import migrations, models


def normalize_text(t):
    return " ".join((t or "").split()).strip().lower()


def compute_chunk_uid(doc_id, content_hash, chunk_index, chunk_text):
    inner = hashlib.sha256(normalize_text(chunk_text).encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{doc_id}|{content_hash}|{chunk_index}|{inner}".encode("utf-8")).hexdigest()


def backfill_chunk_uid(apps, schema_editor):
    EmbeddingChunk = apps.get_model("copilot", "EmbeddingChunk")
    Document = apps.get_model("copilot", "Document")
    doc_cache = {}
    batch_size = 500
    to_update = []
    for ch in EmbeddingChunk.objects.filter(chunk_uid="").iterator(chunk_size=batch_size):
        if ch.document_id not in doc_cache:
            doc = Document.objects.filter(id=ch.document_id).values_list("content_hash", flat=True).first()
            doc_cache[ch.document_id] = doc or ""
        content_hash = doc_cache[ch.document_id]
        uid = compute_chunk_uid(ch.document_id, content_hash, ch.chunk_index, ch.text or "")
        to_update.append((ch.pk, uid))
        if len(to_update) >= batch_size:
            for pk, uid in to_update:
                EmbeddingChunk.objects.filter(pk=pk).update(chunk_uid=uid)
            to_update = []
    for pk, uid in to_update:
        EmbeddingChunk.objects.filter(pk=pk).update(chunk_uid=uid)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("copilot", "0006_document_file_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="embeddingchunk",
            name="chunk_uid",
            field=models.CharField(blank=True, db_index=True, default="", max_length=80),
        ),
        migrations.RunPython(backfill_chunk_uid, noop),
    ]

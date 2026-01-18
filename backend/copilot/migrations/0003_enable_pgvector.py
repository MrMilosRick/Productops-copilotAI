from django.db import migrations
from pgvector.django import VectorExtension

class Migration(migrations.Migration):
    dependencies = [
        ("copilot", "0002_embeddingchunk_uq_embeddingchunk_doc_chunk"),
    ]

    operations = [
        VectorExtension(),
    ]

# Enforce chunk_uid uniqueness without table rewrite (CONCURRENTLY)
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("copilot", "0007_embeddingchunk_chunk_uid"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_embeddingchunk_chunk_uid ON copilot_embeddingchunk (chunk_uid) WHERE chunk_uid <> '';",
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS uq_embeddingchunk_chunk_uid;",
        ),
    ]

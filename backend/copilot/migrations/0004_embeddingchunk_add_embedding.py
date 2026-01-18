from django.db import migrations
from pgvector.django import VectorField

class Migration(migrations.Migration):
    dependencies = [
        ("copilot", "0003_enable_pgvector"),
    ]

    operations = [
        migrations.AddField(
            model_name="embeddingchunk",
            name="embedding",
            field=VectorField(dimensions=1536, null=True, blank=True),
        ),
    ]

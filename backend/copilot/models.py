from django.conf import settings
from django.db import models
from pgvector.django import VectorField

class Workspace(models.Model):
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    role = models.CharField(max_length=32, default="member")  # member/admin
    monthly_cost_limit_usd = models.DecimalField(max_digits=10, decimal_places=2, default=10)

class KnowledgeSource(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    kind = models.CharField(max_length=50, default="upload")  # upload/confluence/jira later
    name = models.CharField(max_length=255)

class Document(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    source = models.ForeignKey(KnowledgeSource, on_delete=models.SET_NULL, null=True, blank=True)

    title = models.CharField(max_length=255)
    filename = models.CharField(max_length=255, blank=True, default="")
    mime = models.CharField(max_length=100, blank=True, default="text/plain")

    content = models.TextField()
    content_hash = models.CharField(max_length=64, db_index=True)

    status = models.CharField(max_length=32, default="uploaded")  # uploaded/chunking/embedded/failed
    chunk_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

class EmbeddingChunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    chunk_index = models.IntegerField()
    text = models.TextField()
    meta = models.JSONField(default=dict, blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"],
                name="uq_embeddingchunk_doc_chunk",
            )
        ]


class IdempotencyKey(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    key = models.CharField(max_length=128, unique=True)
    request_hash = models.CharField(max_length=64)
    run = models.ForeignKey("AgentRun", on_delete=models.SET_NULL, null=True, blank=True)
    response_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AgentRun(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    question = models.TextField()
    mode = models.CharField(max_length=32, default="answer")  # answer/document/automation
    status = models.CharField(max_length=32, default="running")  # running/success/error
    final_output = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")

    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

class AgentStep(models.Model):
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="steps")
    name = models.CharField(max_length=64)
    input_json = models.JSONField(default=dict, blank=True)
    output_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, default="ok")  # ok/error
    created_at = models.DateTimeField(auto_now_add=True)

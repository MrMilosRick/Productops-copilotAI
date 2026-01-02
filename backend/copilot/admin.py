from django.contrib import admin
from .models import (
    Workspace, UserProfile, KnowledgeSource, Document, EmbeddingChunk,
    AgentRun, AgentStep, IdempotencyKey
)

admin.site.register(Workspace)
admin.site.register(UserProfile)
admin.site.register(KnowledgeSource)
admin.site.register(Document)
admin.site.register(EmbeddingChunk)
admin.site.register(AgentRun)
admin.site.register(AgentStep)
admin.site.register(IdempotencyKey)

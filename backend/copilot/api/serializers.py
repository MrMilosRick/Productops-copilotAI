from rest_framework import serializers
from copilot.models import Document, AgentRun, AgentStep

class UploadTextSerializer(serializers.Serializer):
    # Backward-compatible:
    # - legacy clients: {"text": "..."}
    # - new clients:    {"content": "...", "title": "..."}
    title = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")

    # accept either
    content = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    text = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)

    def validate(self, attrs):
        content = attrs.get("content")
        text = attrs.get("text")
        normalized = content if (content is not None and str(content).strip() != "") else text
        if normalized is None or str(normalized).strip() == "":
            raise serializers.ValidationError("content is required")

        attrs["content"] = normalized
        attrs.pop("text", None)
        return attrs

class AskSerializer(serializers.Serializer):
    question = serializers.CharField(required=True)
    mode = serializers.ChoiceField(choices=["answer", "document", "automation"], default="answer")
    answer_mode = serializers.ChoiceField(choices=["sources_only","deterministic","langchain_rag","answer","llm"], required=False, default="sources_only")
    retriever = serializers.ChoiceField(choices=["auto","vector","keyword","hybrid"], default="auto", required=False)
    top_k = serializers.IntegerField(required=False, default=5, min_value=1, max_value=50)
    document_id = serializers.IntegerField(required=False, allow_null=True)

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "title", "filename", "mime", "status", "chunk_count", "created_at"]

class AgentRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentRun
        fields = [
            "id",
            "question",
            "mode",
            "status",
            "cost_usd",
            "prompt_tokens",
            "completion_tokens",
            "created_at",
        ]

class AgentRunDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentRun
        fields = [
            "id",
            "question",
            "mode",
            "status",
            "final_output",
            "error",
            "cost_usd",
            "prompt_tokens",
            "completion_tokens",
            "created_at",
        ]

class AgentStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentStep
        fields = [
            "id",
            "name",
            "status",
            "input_json",
            "output_json",
            "created_at",
        ]


try:
    from copilot.models import Run  # type: ignore
except ImportError:
    from copilot.models import AgentRun as Run  # type: ignore
from copilot.models import AgentStep

class RunSerializer(serializers.ModelSerializer):
    class Meta:
        model = Run
        fields = "__all__"

class AgentStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentStep
        fields = "__all__"

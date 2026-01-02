from rest_framework import serializers
from copilot.models import Document, AgentRun, AgentStep

class UploadTextSerializer(serializers.Serializer):
    title = serializers.CharField(required=True)
    content = serializers.CharField(required=True)

class AskSerializer(serializers.Serializer):
    question = serializers.CharField(required=True)
    mode = serializers.ChoiceField(choices=["answer", "document", "automation"], default="answer")

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

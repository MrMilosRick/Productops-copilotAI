import hashlib
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from copilot.models import Workspace, KnowledgeSource, Document, AgentRun, AgentStep, IdempotencyKey
from copilot.api.serializers import (
    UploadTextSerializer,
    DocumentSerializer,
    AskSerializer,
    AgentRunSerializer,
    AgentRunDetailSerializer,
    AgentStepSerializer,
)
from copilot.tasks import process_document
from copilot.services.retriever import keyword_retrieve
from copilot.services.embeddings import embed_texts
from copilot.services.vector_retriever import vector_retrieve
from copilot.services.hybrid_retriever import hybrid_retrieve
from copilot.services.idempotency import normalize_idempotency_key
from copilot.services.llm import rag_answer_openai
import uuid

@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def deterministic_synthesis(question: str, retrieved: list[dict]) -> str:
    """Deterministic fallback: stitch top snippets and add source refs [i]."""
    if not retrieved:
        return "No sources found."

    # Keep a few top sources; prefer already-sorted by final_score
    top = retrieved[:5]

    parts = []
    for i, r in enumerate(top, start=1):
        snippet = (r.get("snippet") or "").strip()
        if snippet:
            parts.append(f"{snippet} [{i}]")

    if not parts:
        return "No useful snippets found in sources."

    # Simple answer: return stitched evidence.
    # (Keeps behavior deterministic + debuggable)
    return " ".join(parts)

def request_hash(payload: dict) -> str:
    """Hash request payload for idempotency safety (must include all behavior-changing fields)."""
    import json

    mode = payload.get("mode")

    # Support both:
    # - ask(): question/retriever/top_k/document_id/answer_mode
    # - kb_upload_text(): title/content (legacy 'text' too)
    if mode == "kb_upload_text" or payload.get("content") is not None or payload.get("text") is not None:
        stable = {
            "mode": mode or "kb_upload_text",
            "workspace_id": payload.get("workspace_id"),
            "actor_id": payload.get("actor_id"),
            "title": payload.get("title"),
            "content": payload.get("content"),
            "text": payload.get("text"),
        }
    else:
        stable = {
            "mode": payload.get("mode"),
            "question": payload.get("question"),
            "retriever": payload.get("retriever"),
            "top_k": payload.get("top_k"),
            "document_id": payload.get("document_id"),
            "answer_mode": payload.get("answer_mode"),
        }

    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get_or_create_default_workspace() -> Workspace:
    ws, _ = Workspace.objects.get_or_create(name="default")
    return ws

def get_or_create_upload_source(ws: Workspace) -> KnowledgeSource:
    src, _ = KnowledgeSource.objects.get_or_create(workspace=ws, kind="upload", name="uploads")
    return src

# --------------------
# KB endpoints
# --------------------

@api_view(["POST"])
def kb_upload_text(request):
    data = request.data if isinstance(request.data, dict) else {}

    ser = UploadTextSerializer(data=data)
    if not ser.is_valid():
        err = ser.errors
        msg = None

        if isinstance(err, dict):
            # prefer our custom shape first
            if "error" in err:
                msg = err["error"]
                if isinstance(msg, list) and msg:
                    msg = msg[0]
            # DRF common shapes
            elif "non_field_errors" in err:
                msg = err["non_field_errors"]
                if isinstance(msg, list) and msg:
                    msg = msg[0]
            elif "content" in err:
                msg = err["content"]
                if isinstance(msg, list) and msg:
                    msg = msg[0]
            elif "text" in err:
                msg = err["text"]
                if isinstance(msg, list) and msg:
                    msg = msg[0]

        if not msg:
            msg = "content is required"

        return Response({"detail": {"error": msg}}, status=400)

    title = (ser.validated_data.get("title") or "").strip()
    content = (ser.validated_data.get("content") or "").strip()
    title_for_hash = title

    ws = get_or_create_default_workspace()

    # --- Idempotency: same key + same request_hash => replay stored response_json
    raw_key = request.headers.get("Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY")
    idem_key = normalize_idempotency_key(raw_key) if raw_key else None

    payload_for_idem = {
        "mode": "kb_upload_text",
        "workspace_id": ws.id,
        "actor_id": (request.user.id if getattr(request.user, "is_authenticated", False) else None),
        "title": title_for_hash,
        "content": content,
    }
    r_hash = request_hash(payload_for_idem)

    if idem_key:
        existing = IdempotencyKey.objects.filter(key=idem_key).first()
        if existing:
            if (existing.request_hash or "") != r_hash:
                return Response(
                    {"detail": {"error": "Idempotency-Key already used for a different request"}, "idempotency_key": idem_key},
                    status=409,
                )
            if existing.response_json is not None:
                return Response(existing.response_json, status=200)
            # fallback: stable response if record exists but response_json missing
            return Response({"detail": {"error": "idempotent replay missing stored response"}}, status=200)

    # --- Create doc + enqueue processing
    if not title:
        title = f"Text Upload #{uuid.uuid4().hex[:8]}"

    src = get_or_create_upload_source(ws)
    doc = Document.objects.create(
        workspace=ws,
        source=src,
        title=title,
        filename="",
        mime="text/plain",
        content=content,
        content_hash=sha256_text(content),
        status="uploaded",
    )
    process_document.delay(doc.id)

    resp = {"document_id": doc.id, "status": "uploaded", "queued": True}

    if idem_key:
        IdempotencyKey.objects.update_or_create(
            key=idem_key,
            defaults={"workspace": ws, "request_hash": r_hash, "run": None, "response_json": resp},
        )

    return Response(resp, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def kb_documents(request):
    ws = get_or_create_default_workspace()
    qs = Document.objects.filter(workspace=ws).order_by("-id")[:50]
    return Response(DocumentSerializer(qs, many=True).data)

@api_view(["GET"])
def kb_document_detail(request, document_id: int):
    ws = get_or_create_default_workspace()
    doc = Document.objects.get(workspace=ws, id=document_id)
    return Response({
        "id": doc.id,
        "title": doc.title,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at,
        "content_preview": doc.content[:500],
    })

# --------------------
# Copilot "ask" (MVP without LLM) + Idempotency v2
# --------------------

def build_answer_from_retrieved(retrieved):
    lines = ["Found relevant context in KB:"]
    for i, r in enumerate(retrieved or [], start=1):
        title = (r or {}).get("document_title", "")
        snip = ((r or {}).get("snippet", "") or "").strip()
        lines.append(f"{i}. [{title}] {snip}")
    return "\n\n".join(lines)

def get_sources_from_run(run: AgentRun):
    step = run.steps.filter(name="retrieve_context").order_by("-id").first()
    if not step:
        return []
    out = step.output_json or {}
    return out.get("results", []) or []

def normalize_source(r: dict) -> dict:
    # normalize payload so sources in replay == sources in normal response
    if not isinstance(r, dict):
        return {}
    return {
        "document_id": r.get("document_id"),
        "document_title": r.get("document_title"),
        "chunk_id": r.get("chunk_id"),
        "chunk_index": r.get("chunk_index"),
        "matched_terms": r.get("matched_terms", []),
        "distance": r.get("distance"),
        "score": r.get("score"),
        "snippet": r.get("snippet"),
        "retriever_hint": r.get("retriever_hint"),
        "vector_score": r.get("vector_score"),
        "keyword_bonus": r.get("keyword_bonus"),
        "keyword_score": r.get("keyword_score"),
        "keyword_norm": r.get("keyword_norm"),
        "final_score": r.get("final_score"),
    }

@api_view(["POST"])
def ask(request):
    ser = AskSerializer(data=request.data)
    try:
        ser.is_valid(raise_exception=True)
    except Exception as e:
        # normalize DRF ValidationError -> {"detail": {"error": "..."}}
        err = getattr(e, "detail", None)
        msg = None
        if isinstance(err, dict):
            # typical shapes: {"field": ["..."]} or {"non_field_errors": ["..."]}
            if "non_field_errors" in err:
                msg = err.get("non_field_errors")
            else:
                # take first field error
                for _k, _v in err.items():
                    msg = _v
                    break
            if isinstance(msg, list) and msg:
                msg = msg[0]
        elif isinstance(err, list) and err:
            msg = err[0]
        if not msg:
            msg = "invalid request"
        _m = str(msg)
        # optional: make a couple messages friendlier/stable
        if _m == "This field is required.":
            _m = "question is required"
        if _m == "This field may not be blank.":
            _m = "question may not be blank"
        return Response({"detail": {"error": _m}}, status=400)

    question = ser.validated_data["question"]
    mode = ser.validated_data.get("mode", "answer")
    retriever = ser.validated_data.get("retriever", "auto")
    top_k = int(ser.validated_data.get("top_k", 5) or 5)
    document_id = ser.validated_data.get("document_id")
    answer_mode = (
        (request.data.get("answer_mode") if isinstance(request.data, dict) else None)
        or ser.validated_data.get("answer_mode")
        or "sources_only"
    )
    # accept UI-friendly alias
    # answer_mode="answer" -> use real implementation branch
    if answer_mode in ("answer", "llm"):
        answer_mode = "langchain_rag"

    # keep run.mode aligned with effective behavior
    if mode == "answer":
        mode = answer_mode

    if document_id is not None:
        document_id = int(document_id)
    ws = get_or_create_default_workspace()

    # Idempotency (optional)
    idem_key = request.headers.get("Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY")
    if idem_key:
        idem_key = normalize_idempotency_key(idem_key)

    actor_id = (int(request.user.id) if getattr(request.user, "is_authenticated", False) else None)

    payload_for_idem = {"workspace_id": getattr(ws, "id", None), "actor_id": actor_id, "mode": mode, "question": question, "retriever": retriever, "top_k": top_k, "document_id": document_id, "answer_mode": answer_mode}

    r_hash = request_hash(payload_for_idem)

    # 1) Idempotency replay
    if idem_key:
        existing = IdempotencyKey.objects.filter(key=idem_key).first()
        if existing:
            if existing.request_hash != r_hash:
                return Response(
                    {
                        "error": "Idempotency-Key already used for a different request",
                        "idempotency_key": idem_key,
                    },
                    status=409,
                )
            if existing.run_id:
                run = AgentRun.objects.get(id=existing.run_id)
                sources = get_sources_from_run(run)

                # best-effort: retriever_used from latest retrieve_context step
                step = AgentStep.objects.filter(run=run, name="retrieve_context").order_by("-id").first()
                retriever_used = ""
                if step and isinstance(step.output_json, dict):
                    retriever_used = step.output_json.get("retriever_used") or ""

                # best-effort: llm_used / answer_mode from generate_answer step
                gen = AgentStep.objects.filter(run=run, name="generate_answer").order_by("-id").first()
                llm_used_prev = getattr(run, "llm_used", None)
                answer_mode_prev = ""
                if gen and isinstance(getattr(gen, "output_json", None), dict):
                    llm_used_prev = (gen.output_json or {}).get("llm_used") or llm_used_prev
                    answer_mode_prev = (gen.output_json or {}).get("answer_mode") or answer_mode_prev

                resp = {
                    "run_id": run.id,
                    "answer": run.final_output or "",
                    "sources": sources,
                    "retriever_used": retriever_used,
                    "llm_used": llm_used_prev or "none",
                    "answer_mode": answer_mode_prev or "",
                    "idempotent_replay": True,
                }
                return Response(resp)
# 2) Create run (new execution)
    run = AgentRun.objects.create(
        workspace=ws,
        user=None,
        question=question,
        mode=mode,
        status="running",
    )

    if idem_key:
        IdempotencyKey.objects.update_or_create(
            key=idem_key,
            defaults={
                "workspace": ws,
                "request_hash": r_hash,
                "run": run,
            },
        )

    try:
        retrieved = []
        retriever_used = "keyword"
        llm_used = "none"

        if retriever == "keyword":
            retrieved = keyword_retrieve(ws.id, question, top_k=top_k)
            retriever_used = "keyword"

        elif retriever == "vector":
            query_vec = embed_texts([question])[0] if (question or "").strip() else []
            retrieved = vector_retrieve(ws.id, query_vec, top_k=top_k, document_id=document_id) if query_vec else []
            retriever_used = "vector"

        elif retriever == "hybrid":
            retrieved = hybrid_retrieve(ws.id, question, top_k=top_k, document_id=document_id)
            retriever_used = "hybrid"

        else:  # auto -> hybrid (default)
            retrieved = hybrid_retrieve(ws.id, question, top_k=top_k, document_id=document_id)
            retriever_used = "hybrid"

        AgentStep.objects.create(
            run=run,
            name="retrieve_context",
            input_json={"question": question, "top_k": top_k, "retriever": retriever, "document_id": document_id},
            output_json={"results": retrieved, "retriever_used": retriever_used},
            status="ok",
        )

        if not retrieved:

            run.status = "success"

            run.final_output = (

                "I couldn't find relevant knowledge in the KB for this question.\n"

                "Try uploading more docs or rephrasing the query."

            )

            run.save(update_fields=["status", "final_output"])

            return Response(

                {

                    "run_id": run.id,


                    "sources": [],

                    "retriever_used": retriever_used,

                    "llm_used": llm_used,

                    "answer_mode": answer_mode,

                }

            )


        run.status = "success"
        if answer_mode == "sources_only":
            llm_used = "none"
            run.final_output = deterministic_synthesis(question, retrieved)
        elif answer_mode == "deterministic":
            llm_used = "none"
            run.final_output = deterministic_synthesis(question, retrieved)
        elif answer_mode == "langchain_rag":
            out = rag_answer_openai(question, retrieved)
            llm_used = out.get("llm_used", "openai")
            run.final_output = out.get("answer", "")
        else:
            run.status = "error"
            run.error = f"unknown answer_mode: {answer_mode}"
            run.save(update_fields=["status","error"])
            return Response({"run_id": run.id, "error": run.error, "sources": retrieved, "retriever_used": retriever_used, "llm_used": "none", "answer_mode": answer_mode}, status=400)
        run.save(update_fields=["status", "final_output"])


        # persist generate_answer step for idempotent replay consistency
        try:
            # store llm_used on run if model has such field
            if hasattr(run, "llm_used"):
                run.llm_used = llm_used
                run.save(update_fields=["llm_used"])
        except Exception:
            pass

        try:
            AgentStep.objects.create(
                run=run,
                name="generate_answer",
                input_json={"question": question, "answer_mode": answer_mode},
                output_json={
                    "llm_used": llm_used,
                    "answer_mode": answer_mode,
                    "answer_preview": (run.final_output or "")[:500],
                },
                status="success",
            )
        except Exception:
            pass

        return Response(
            {"run_id": run.id, "answer": run.final_output, "sources": retrieved, "retriever_used": retriever_used, "llm_used": llm_used, "answer_mode": answer_mode}
        )

    except Exception as e:
        AgentStep.objects.create(
            run=run,
            name="retrieve_context",
            input_json={"question": question},
            output_json={"error": str(e)},
            status="error",
        )
        run.status = "error"
        run.error = str(e)
        run.save(update_fields=["status", "error"])
        return Response({"run_id": run.id, "llm_used": llm_used,  "error": str(e)}, status=500)

# --------------------
# Traces API
# --------------------

@api_view(["GET"])
def runs_list(request):
    ws = get_or_create_default_workspace()
    qs = AgentRun.objects.filter(workspace=ws).order_by("-id")[:50]
    return Response(AgentRunSerializer(qs, many=True).data)

@api_view(["GET"])
def run_detail(request, run_id: int):
    ws = get_or_create_default_workspace()
    run = AgentRun.objects.get(workspace=ws, id=run_id)
    return Response(AgentRunDetailSerializer(run).data)

@api_view(["GET"])
def run_steps(request, run_id: int):
    ws = get_or_create_default_workspace()
    run = AgentRun.objects.get(workspace=ws, id=run_id)
    steps = run.steps.order_by("id")
    return Response(AgentStepSerializer(steps, many=True).data)

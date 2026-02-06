import hashlib
import re
from rest_framework.decorators import api_view, parser_classes
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status

from copilot.models import Workspace, KnowledgeSource, Document, AgentRun, AgentStep, IdempotencyKey, EmbeddingChunk
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
from copilot.services.llm import rag_answer_openai, general_answer_openai, repair_fallback_openai, repair_doc_answer_openai, _strip_noise_sections, _normalize_general_output
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
    raw_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY") or request.META.get("HTTP_X_IDEMPOTENCY_KEY")
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




@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def kb_upload_file(request):
    """
    Multipart upload:
      - file: multipart field 'file'
      - title: optional (fallback to filename)

    Stores file under MEDIA_ROOT/ws_<id>/ and extracts text:
      - PDF: pypdf first, then pdfminer.six fallback
      - non-PDF: utf-8 decode (best-effort)
    """
    ws = get_or_create_default_workspace()
    src = get_or_create_upload_source(ws)

    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": {"error": "file is required (multipart field 'file')"}}, status=400)

    title = (request.POST.get("title") or upload.name or "Upload").strip()
    filename = upload.name or ""
    mime = getattr(upload, "content_type", "") or ""

    from django.conf import settings
    from pathlib import Path as _Path

    ws_dir = _Path(settings.MEDIA_ROOT) / f"ws_{ws.id}"
    ws_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (filename or "upload").replace("/", "_").replace("\\", "_")
    file_path = ws_dir / safe_name

    with open(file_path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    lower = safe_name.lower()

    # --- extract text (PDF: worker extracts via pdfminer in process_document) ---
    text = ""
    if lower.endswith(".pdf") or mime == "application/pdf":
        # Leave content empty; worker will extract via pdfminer in process_document
        pass
    else:
        try:
            data = file_path.read_bytes()
            text = data.decode("utf-8", errors="replace").strip()
        except Exception as e:
            return Response({"detail": {"error": "failed to read file: " + e.__class__.__name__ + ": " + str(e)}}, status=400)

    text = (text or "").replace("\x00", "")
    is_pdf = lower.endswith(".pdf") or mime == "application/pdf"
    if not text and not is_pdf:
        return Response({"detail": {"error": "extracted text is empty"}}, status=400)

    # persist doc + enqueue embedding
    content_hash = sha256_text(text)
    doc = Document.objects.create(
        workspace=ws,
        source=src,
        title=title,
        filename=filename,
        mime=(mime or ("application/pdf" if lower.endswith(".pdf") else "application/octet-stream")),
        file_path=str(file_path),
        content=text,
        content_hash=content_hash,
        status="uploaded",
    )
    process_document.delay(doc.id)
    return Response({"document_id": doc.id, "status": doc.status, "queued": True}, status=201)

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

def sanitize_sources(items):
    """Copy each source dict and remove full chunk text to avoid leaking in API/DB."""
    out = [dict(r or {}) for r in (items or [])]
    for d in out:
        d.pop("text", None)
    return out

def get_sources_from_run(run: AgentRun):
    step = run.steps.filter(name="retrieve_context").order_by("-id").first()
    if not step:
        return []
    out = step.output_json or {}
    return sanitize_sources(out.get("results", []) or [])


def _validate_and_repair_fallback(question: str, draft: str) -> str:
    """Validate fallback answer; if invalid, call repair_fallback_openai and return repaired answer."""
    q = (question or "").strip()
    d = (draft or "").strip()
    if not d:
        return draft
    expected_first = "В данном документе нет информации, чтобы ответить на: " + q + "."
    first_line = (d.split("\n")[0] or "").strip()
    if first_line != expected_first:
        out = repair_fallback_openai(question, draft)
        return (out.get("answer") or "").strip() or draft
    if "В данном документе нет прямого ответа" in d:
        out = repair_fallback_openai(question, draft)
        return (out.get("answer") or "").strip() or draft
    d_lower = d.lower()
    for w in ("возможно", "может быть", "возможен", "иногда"):
        if w in d_lower:
            out = repair_fallback_openai(question, draft)
            return (out.get("answer") or "").strip() or draft
    q_lower = q.lower()
    if "главн" in q_lower and any(x in q_lower for x in ("героин", "герой", "персонаж")):
        for m in ("болезн", "расстройств", "зависим", "диагноз", "псих", "мед"):
            if m in d_lower:
                out = repair_fallback_openai(question, draft)
                return (out.get("answer") or "").strip() or draft
    lines = [l for l in d.splitlines() if l.strip()]
    if len(lines) > 14:
        out = repair_fallback_openai(question, draft)
        return (out.get("answer") or "").strip() or draft
    return draft


def _build_doc_context(retrieved: list) -> str:
    """Build context string exactly like rag_answer_openai for retrieved snippets."""
    retrieved = (retrieved or [])[:5]
    ctx_lines = []
    for i, r in enumerate(retrieved, start=1):
        title = (r or {}).get("document_title", "")
        block = ((r or {}).get("text") or (r or {}).get("snippet") or "").strip()
        block = block[:3500]
        ctx_lines.append(f"[{i}] {title}\n{block}")
    return "\n\n".join(ctx_lines).strip()


def _validate_and_repair_doc_answer(question: str, retrieved: list, draft: str) -> tuple:
    """Validate doc answer format; if invalid, call repair_doc_answer_openai. Returns (answer_str, llm_used_if_repaired_or_None)."""
    d = (draft or "").strip()
    if not d:
        return (draft, None)
    if "Ответ:" not in d or "Детали:" not in d or "Источники:" not in d:
        try:
            context = _build_doc_context(retrieved)
            out = repair_doc_answer_openai(question, context, draft)
            return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
        except Exception:
            return (draft, None)
    refusal = "В документе нет прямого ответа на этот вопрос." in d
    detali_idx = d.find("Детали:")
    istoki_idx = d.find("Источники:")
    if detali_idx == -1 or istoki_idx == -1:
        try:
            context = _build_doc_context(retrieved)
            out = repair_doc_answer_openai(question, context, draft)
            return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
        except Exception:
            return (draft, None)
    detali_section = d[detali_idx:istoki_idx]
    bullet_lines = [ln.strip() for ln in detali_section.splitlines() if ln.strip().startswith(("-", "•"))]
    if len(bullet_lines) < 3:
        try:
            context = _build_doc_context(retrieved)
            out = repair_doc_answer_openai(question, context, draft)
            return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
        except Exception:
            return (draft, None)
    citation_re = re.compile(r"\[\d+\]")
    for bl in bullet_lines:
        if not citation_re.search(bl):
            try:
                context = _build_doc_context(retrieved)
                out = repair_doc_answer_openai(question, context, draft)
                return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
            except Exception:
                return (draft, None)
        has_quote = "«" in bl or "»" in bl or '"' in bl
        if not has_quote:
            try:
                context = _build_doc_context(retrieved)
                out = repair_doc_answer_openai(question, context, draft)
                return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
            except Exception:
                return (draft, None)
    istoki_section = d[istoki_idx:]
    istoki_items = [ln.strip() for ln in istoki_section.splitlines() if ln.strip() and not ln.strip().startswith("Источники:")]
    if not refusal and len(istoki_items) < 2:
        try:
            context = _build_doc_context(retrieved)
            out = repair_doc_answer_openai(question, context, draft)
            return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
        except Exception:
            return (draft, None)
    return (draft, None)


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
    idem_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY") or request.META.get("HTTP_X_IDEMPOTENCY_KEY")
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

                step_out = (step.output_json or {}) if step else {}
                route_replay = step_out.get("route") or ("summary" if retriever_used == "summary" else "")
                notice_replay = step_out.get("notice") or ""
                resp = {
                    "run_id": run.id,
                    "answer": _strip_noise_sections(run.final_output or ""),
                    "sources": sources,
                    "retriever_used": retriever_used,
                    "llm_used": llm_used_prev or "none",
                    "answer_mode": answer_mode_prev or "",
                    "route": route_replay,
                    "notice": notice_replay,
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

        summary_triggers = ("о чем", "про что", "кратко", "краткое содержание", "summary", "summarize", "обзор")
        q_lower = (question or "").strip().lower()
        is_summary = document_id is not None and answer_mode != "sources_only" and any(t in q_lower for t in summary_triggers)

        if is_summary:
            chunks_qs = EmbeddingChunk.objects.filter(document_id=document_id).select_related("document").order_by("chunk_index")
            chunks = list(chunks_qs)
            if not chunks:
                run.status = "success"
                run.final_output = "Нет фрагментов в документе."
                run.save(update_fields=["status", "final_output"])
                return Response({
                    "run_id": run.id,
                    "answer": _strip_noise_sections(run.final_output or ""),
                    "sources": [],
                    "retriever_used": "summary",
                    "llm_used": "none",
                    "answer_mode": answer_mode,
                    "route": "summary",
                    "notice": "",
                })
            n = len(chunks)
            if n <= 12:
                selected = chunks
            else:
                selected = [chunks[int(round(i * (n - 1) / 11))] for i in range(12)]
            retrieved = []
            for ch in selected:
                txt = (ch.text or "")[:3500]
                retrieved.append({
                    "document_id": ch.document_id,
                    "document_title": ch.document.title,
                    "chunk_id": ch.id,
                    "chunk_index": ch.chunk_index,
                    "snippet": (ch.text or "")[:300],
                    "text": txt,
                })
            AgentStep.objects.create(
                run=run,
                name="retrieve_context",
                input_json={"question": question, "document_id": document_id},
                output_json={"results": sanitize_sources(retrieved), "retriever_used": "summary"},
                status="ok",
            )
            out = rag_answer_openai(question, retrieved)
            llm_used = out.get("llm_used", "openai")
            run.status = "success"
            run.final_output = out.get("answer", "")
            run.save(update_fields=["status", "final_output"])
            return Response({
                "run_id": run.id,
                "answer": _strip_noise_sections(run.final_output or ""),
                "sources": sanitize_sources(retrieved),
                "retriever_used": "summary",
                "llm_used": llm_used,
                "answer_mode": answer_mode,
                "route": "summary",
                "notice": "",
            })

        if retriever == "keyword":
            retrieved = keyword_retrieve(ws.id, question, top_k=top_k, document_id=document_id)
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

        V_THR = 0.55
        V_HARD = 0.70
        KW_THR = 4
        if retriever_used == "keyword":
            best_kw = max((float(r.get("score") or 0) for r in retrieved), default=0)
        else:
            best_kw = max((float(r.get("keyword_score") or 0) for r in retrieved), default=0)
        best_vec = max((float(r.get("vector_score") or 0) for r in retrieved), default=0)
        mt_list = (r.get("matched_terms") or [] for r in retrieved)
        has_kw_hit = (best_kw > 0) or any(len(m) > 0 if isinstance(m, (list, tuple)) else bool(m) for m in mt_list)
        relevant = (best_kw >= KW_THR) or (has_kw_hit and best_vec >= V_THR) or (best_vec >= V_HARD)
        max_score = max((float(r.get("final_score") or r.get("vector_score") or r.get("score") or 0) for r in retrieved), default=0)
        # Hard gate: keep NO-DOC out of doc_rag, but don't over-prune borderline DOC queries.
        # "велосипед" had max_score≈0.52, so 0.55 still routes to general.
        if max_score < 0.55:
            relevant = False
        debug_payload = {
            "best_kw": best_kw,
            "best_vec": best_vec,
            "relevant": relevant,
            "has_kw_hit": has_kw_hit,
            "V_THR": V_THR,
            "V_HARD": V_HARD,
            "KW_THR": KW_THR,
            "retriever_used": retriever_used,
            "retriever_requested": retriever,
            "document_id": document_id,
            "top_k": top_k,
        }

        if answer_mode == "sources_only":
            AgentStep.objects.create(
                run=run,
                name="retrieve_context",
                input_json={"question": question, "top_k": top_k, "retriever": retriever, "document_id": document_id},
                output_json={"results": sanitize_sources(retrieved), "retriever_used": retriever_used, "route": "doc_rag", "notice": "", "debug": debug_payload},
                status="ok",
            )
            return Response({
                "run_id": run.id,
                "answer": _strip_noise_sections(""),
                "sources": sanitize_sources(retrieved),
                "retriever_used": retriever_used,
                "llm_used": "none",
                "answer_mode": answer_mode,
                "route": "doc_rag",
                "notice": "",
                "debug": debug_payload,
            })

        if not retrieved:
            if document_id is not None:
                notice = ""
                out = general_answer_openai(question)
                general_answer = out.get("answer", "")
                general_answer = _validate_and_repair_fallback(question, general_answer)
                llm_used = out.get("llm_used", "openai")
                AgentStep.objects.create(
                    run=run,
                    name="retrieve_context",
                    input_json={"question": question, "top_k": top_k, "retriever": retriever, "document_id": document_id},
                    output_json={
                        "results": [],
                        "retriever_used": "general",
                        "route": "general",
                        "best_kw": best_kw,
                        "best_vec": best_vec,
                        "retriever_requested": retriever,
                        "notice": notice,
                        "debug": debug_payload,
                    },
                    status="ok",
                )
                run.status = "success"
                run.final_output = general_answer
                run.save(update_fields=["status", "final_output"])
                try:
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
                        output_json={"llm_used": llm_used, "answer_mode": answer_mode, "route": "general", "answer_preview": (run.final_output or "")[:500]},
                        status="success",
                    )
                except Exception:
                    pass
                return Response({
                    "run_id": run.id,
                    "answer": _strip_noise_sections(_normalize_general_output(run.final_output or "", question or "")),
                    "sources": [],
                    "retriever_used": "general",
                    "llm_used": llm_used,
                    "answer_mode": answer_mode,
                    "route": "general",
                    "notice": notice,
                    "debug": debug_payload,
                })
            run.status = "success"
            run.final_output = (
                "I couldn't find relevant knowledge in the KB for this question.\n"
                "Try uploading more docs or rephrasing the query."
            )
            run.save(update_fields=["status", "final_output"])
            return Response({
                "run_id": run.id,
                "answer": _strip_noise_sections(run.final_output or ""),
                "sources": [],
                "retriever_used": retriever_used,
                "llm_used": llm_used,
                "answer_mode": answer_mode,
                "route": "doc_rag",
                "notice": "",
                "debug": debug_payload,
            })

        if not relevant:
            notice = ""
            out = general_answer_openai(question)
            general_answer = out.get("answer", "")
            general_answer = _validate_and_repair_fallback(question, general_answer)
            llm_used = out.get("llm_used", "openai")
            AgentStep.objects.create(
                run=run,
                name="retrieve_context",
                input_json={"question": question, "top_k": top_k, "retriever": retriever, "document_id": document_id},
                output_json={
                    "results": [],
                    "retriever_used": "general",
                    "route": "general",
                    "best_kw": best_kw,
                    "best_vec": best_vec,
                    "retriever_requested": retriever,
                    "notice": notice,
                    "debug": debug_payload,
                },
                status="ok",
            )
            run.status = "success"
            run.final_output = general_answer
            run.save(update_fields=["status", "final_output"])
            try:
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
                    output_json={"llm_used": llm_used, "answer_mode": answer_mode, "route": "general", "answer_preview": (run.final_output or "")[:500]},
                    status="success",
                )
            except Exception:
                pass
            return Response({
                "run_id": run.id,
                "answer": _strip_noise_sections(_normalize_general_output(run.final_output or "", question or "")),
                "sources": [],
                "retriever_used": "general",
                "llm_used": llm_used,
                "answer_mode": answer_mode,
                "route": "general",
                "notice": notice,
                "debug": debug_payload,
            })

        AgentStep.objects.create(
            run=run,
            name="retrieve_context",
            input_json={"question": question, "top_k": top_k, "retriever": retriever, "document_id": document_id},
            output_json={"results": sanitize_sources(retrieved), "retriever_used": retriever_used, "route": "doc_rag", "notice": "", "debug": debug_payload},
            status="ok",
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
            repaired, repair_llm = _validate_and_repair_doc_answer(question, retrieved, run.final_output)
            run.final_output = repaired
            if repair_llm is not None:
                llm_used = repair_llm
        else:
            run.status = "error"
            run.error = f"unknown answer_mode: {answer_mode}"
            run.save(update_fields=["status","error"])
            return Response({"run_id": run.id, "error": run.error, "sources": sanitize_sources(retrieved), "retriever_used": retriever_used, "llm_used": "none", "answer_mode": answer_mode}, status=400)
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
            {"run_id": run.id, "answer": _strip_noise_sections(run.final_output or ""), "sources": sanitize_sources(retrieved), "retriever_used": retriever_used, "llm_used": llm_used, "answer_mode": answer_mode, "route": "doc_rag", "notice": "", "debug": debug_payload}
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
from django.http import JsonResponse

def api_index(request):
    return JsonResponse({
        "service": "ProductOps Copilot API",
        "endpoints": {
            "health": "/api/health/",
            "upload_text": "/api/kb/upload_text/",
            "documents": "/api/kb/documents/",
            "document_detail": "/api/kb/documents/<id>/",
            "ask": "/api/ask/",
            "runs": "/api/runs/",
            "run_detail": "/api/runs/<id>/",
            "run_steps": "/api/runs/<id>/steps/",
        },
        "quickstart": {
            "health": "curl -fsS http://localhost:8001/api/health/ | jq .",
            "upload_text": "curl -fsS -X POST http://localhost:8001/api/kb/upload_text/ -H 'Content-Type: application/json' -d '{\"title\":\"t\",\"content\":\"hello\"}' | jq .",
        }
    })

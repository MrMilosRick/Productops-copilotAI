import hashlib
import re
import uuid
from typing import Optional

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
from copilot.services.llm import (
    rag_answer_openai,
    general_answer_openai,
    repair_fallback_openai,
    repair_doc_answer_openai,
    _strip_noise_sections,
    _normalize_general_output,
    detect_lang,
)

FIRST_PERSON_PATTERNS = (
    "меня зовут",
    "обо мне",
    "о себе",
    "немного фактов обо мне",
)
SOFT_VEC_DOC = 0.45

DOC_META_ANCHORS = (
    "документ",
    "текст",
    "document",
)

DOC_META_INTENTS = (
    "как называется",
    "название",
    "title",
    "name",
    "что это за",
    "what is this",
    "о чем",
    "о чём",
    "about this",
    "как заканчивается",
    "чем заканчивается",
    "ending",
    "финал",
)

DOC_TITLE_INTENTS = (
    "как называется",
    "название",
    "title",
    "name",
)


def _norm_q(s: str) -> str:
    """
    Minimal RU normalization for intent matching:
    - lowercasing
    - "ё" -> "е" (so "чём" matches "чем"/"о чем")
    """
    return (s or "").strip().lower().replace("ё", "е")


RU_TRIVIAL_TERMS = {
    "есть",  # common verb, causes false keyword hits (e.g., "как есть шаурму")
    "это",
    "как",
    "что",
    "где",
    "когда",
    "делать",
}


def _is_authorish_question(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    ru = ("кто автор", "автор", "кто написал", "кто пишет", "как зовут", "имя автора")
    en = ("who is the author", "author", "who wrote", "who writes", "what is your name", "who are you")
    return any(k in q for k in (ru + en))


def _has_nontrivial_kw_terms(retrieved: list) -> bool:
    for r in (retrieved or []):
        terms = (r or {}).get("matched_terms") or []
        for t in terms:
            tt = (t or "").strip().lower()
            if len(tt) < 3:
                continue
            if tt in RU_TRIVIAL_TERMS:
                continue
            return True
    return False


def _is_doc_metadata_question(question: str) -> bool:
    q = (question or "").strip().lower()
    return bool(q) and any(a in q for a in DOC_META_ANCHORS) and any(i in q for i in DOC_META_INTENTS)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def deterministic_synthesis(question: str, retrieved: list[dict]) -> str:
    """Deterministic fallback: stitch top snippets and add source refs [i]."""
    if not retrieved:
        return "No sources found."

    top = retrieved[:5]
    parts = []
    for i, r in enumerate(top, start=1):
        snippet = (r.get("snippet") or r.get("text") or "").strip()
        if snippet:
            parts.append(f"{snippet} [{i}]")

    if not parts:
        return "No useful snippets found in sources."

    q_lower = (question or "").strip().lower()
    is_howto = (
        q_lower.startswith("как ") or q_lower.startswith("каким образом ")
        or "шаг" in q_lower or "инструкц" in q_lower
    )
    if is_howto:
        snips: list[tuple[int, str]] = []
        for idx, r in enumerate(top, start=1):
            s = (r.get("snippet") or r.get("text") or "").strip()
            if s:
                snips.append((idx, s))
        if not snips:
            return " ".join(parts)
        first = snips[0][1]
        dot = first.find(". ", 10)
        if dot > 0:
            answer_sent = first[: dot + 1].strip()
        else:
            words = first.split()
            answer_sent = " ".join(words[:25]) + ("..." if len(words) > 25 else "")
        detail_bullets = []
        for src_i, s in snips[:5]:
            if ":" in s:
                colon_pos = s.find(":")
                after = s[colon_pos + 1 :].strip()
                items = [x.strip() for x in after.split(",") if x.strip()]
                if len(items) >= 2:
                    take = min(5, len(items))
                    for item in items[:take]:
                        item = (item or "").rstrip(" .;")
                        if item:
                            detail_bullets.append(f"- {item} [{src_i}]")
                    continue
            step = (s[:80] + "..." if len(s) > 80 else s).strip()
            if step:
                detail_bullets.append(f"- {step} [{src_i}]")
        if len(detail_bullets) > 5:
            detail_bullets = detail_bullets[:5]
        source_bullets = []
        for src_i, s in snips[:3]:
            short = " ".join(s.split()[:25])
            if len(s.split()) > 25:
                short += "..."
            if short:
                source_bullets.append(f"- {short} [{src_i}]")
        lines = [
            f"Ответ: {answer_sent}",
            "",
            "Детали:",
            *detail_bullets,
            "",
            "Источники:",
            *source_bullets,
        ]
        non_empty = [ln for ln in lines if ln.strip()]
        while len(non_empty) > 14 and detail_bullets:
            detail_bullets.pop()
            lines = [
                f"Ответ: {answer_sent}",
                "",
                "Детали:",
                *detail_bullets,
                "",
                "Источники:",
                *source_bullets,
            ]
            non_empty = [ln for ln in lines if ln.strip()]
        if len(non_empty) > 14 and source_bullets:
            source_bullets.pop()
            lines = [
                f"Ответ: {answer_sent}",
                "",
                "Детали:",
                *detail_bullets,
                "",
                "Источники:",
                *source_bullets,
            ]
            non_empty = [ln for ln in lines if ln.strip()]
        return "\n".join(lines)

    return " ".join(parts)


def _has_first_person_intro(chunks: list[dict] | None) -> bool:
    for r in (chunks or [])[:3]:
        snippet = (r.get("snippet") or "").lower()
        text = (r.get("text") or "").lower()
        blob = f"{snippet} {text}"
        for pat in FIRST_PERSON_PATTERNS:
            if pat in blob:
                return True
    return False


def _is_doc_metadata_question(question: str) -> bool:
    q = _norm_q(question)
    return bool(q) and any(a in q for a in DOC_META_ANCHORS) and any(i in q for i in DOC_META_INTENTS)

def _is_doc_title_question(question: str) -> bool:
    q = _norm_q(question)
    return bool(q) and any(a in q for a in DOC_META_ANCHORS) and any(i in q for i in DOC_TITLE_INTENTS)



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


def _add_out_of_doc_notice(notice: str, document_id: Optional[int]) -> str:
    if document_id is not None and not (notice or "").strip():
        return "out_of_document"
    return notice


def _wants_list(question: str) -> bool:
    q = (question or "").lower()
    return any(k in q for k in ("список", "списком", "перечисл", "перечень", "пункт", "буллет", "bullet"))


def _strip_inline_citations(s: str) -> str:
    return re.sub(r"\s*\[\d+\]\s*", " ", (s or "")).strip()


def _extract_cited_indices(text: str) -> set:
    cited = set()
    for m in re.findall(r"\[(\d+)\]", text or ""):
        try:
            cited.add(int(m))
        except Exception:
            continue
    return cited


def _filter_sources_by_citations(answer_with_citations: str, sources: list, max_items: int = 3) -> list:
    """Keep only sources whose 1-based index [i] is cited in the answer text. If no citations -> first max_items."""
    srcs = list(sources or [])
    if not srcs:
        return []
    cited = _extract_cited_indices(answer_with_citations or "")
    if cited:
        filtered = [it for idx, it in enumerate(srcs, start=1) if idx in cited]
        if filtered:
            return filtered[:max_items]
    return srcs[:max_items]


def _trim_doc_answer_sections(structured_text: str) -> str:
    """Keep only the answer block; drop Quotes/Цитаты and Sources/Источники sections."""
    t = (structured_text or "").strip()
    if not t:
        return ""
    stop_heads = (
        "источники:",
        "sources:",
        "источник:",
        "source:",
        "цитаты:",
        "quotes:",
    )
    kept = []
    for raw in t.splitlines():
        line = (raw or "").strip()
        if line and any(line.lower().startswith(h) for h in stop_heads):
            break
        kept.append(raw)
    return "\n".join(kept).strip()

def _format_doc_answer(question: str, structured_text: str, max_lines: int = 2) -> str:
    """UX contract for doc_rag: 1–2 lines, strictly from document, NO headings, no inline [n] in answer."""
    t = (structured_text or "").strip()
    if not t:
        return ""
    answer_line = ""
    detail_lines = []
    stop_heads = (
        "источники:",
        "sources:",
        "источник:",
        "source:",
        "цитаты:",
        "quotes:",
    )
    in_details = False
    for raw in t.splitlines():
        line = (raw or "").strip()
        low = line.lower()
        if not line:
            continue
        if any(low.startswith(h) for h in stop_heads):
            break
        if low.startswith("ответ:"):
            answer_line = line.split(":", 1)[1].strip()
            in_details = False
            continue
        if low.startswith("answer:"):
            answer_line = line.split(":", 1)[1].strip()
            in_details = False
            continue
        if low.startswith("детали:"):
            in_details = True
            continue
        if in_details and line.startswith(("-", "•")):
            detail_lines.append(line.lstrip("-•").strip())
    base = _strip_inline_citations(answer_line) or _strip_inline_citations(t.splitlines()[0] if t.splitlines() else t)
    base = " ".join(base.split())
    wants_list = _wants_list(question)
    if wants_list and detail_lines:
        facts = []
        for dl in detail_lines:
            dl = _strip_inline_citations(dl)
            dl = " ".join((dl or "").split())
            if dl:
                facts.append(dl)
        facts_text = "; ".join(facts).strip(" ;")
        lines = [base, facts_text] if facts_text else [base]
    else:
        lines = [base]
    out = []
    for ln in lines:
        ln = (ln or "").strip()
        # Fix common spacing artifacts before punctuation (e.g., "Arina ." / "Арина .")
        ln = re.sub(r"\s+([.,!?;:])", r"\1", ln)
        # Also normalize multiple spaces just in case
        ln = re.sub(r"\s{2,}", " ", ln).strip()
        if ln:
            out.append(ln)
        if len(out) >= max_lines:
            break
    return "\n".join(out).strip()


def get_sources_from_run(run: AgentRun):
    step = run.steps.filter(name="retrieve_context").order_by("-id").first()
    if not step:
        return []
    out = step.output_json or {}
    return sanitize_sources(out.get("results", []) or [])


# --------------------
# Language helpers (minimal, no deps)
# --------------------
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def _detect_lang(text: Optional[str]) -> str:
    """Return 'ru' if text contains Cyrillic, else 'en'."""
    return "ru" if text and CYRILLIC_RE.search(text) else "en"


def _general_answer_deterministic(question: str) -> str:
    q = " ".join((question or "").strip().split())
    lang = _detect_lang(q)
    if lang == "ru":
        return f"В документе нет информации по вопросу: {q}."
    return f"This document does not contain information about {q}."


def _validate_and_repair_fallback(question: str, draft: str) -> str:
    """Validate fallback answer; if invalid, call repair_fallback_openai and return repaired answer."""
    q = (question or "").strip()
    d = (draft or "").strip()
    if not d:
        return draft
    # IMPORTANT: repair_fallback_openai is RU-only today (llm.py), so for EN questions
    # we must NOT enforce RU templates or call RU repair. Formatting is handled in ensure_general_sections().
    if _detect_lang(question) == "en":
        return draft
    expected_legacy = "В данном документе нет информации, чтобы ответить на: " + q + "."
    first_line = (d.split("\n")[0] or "").strip()
    if first_line != expected_legacy and not first_line.startswith("В этом документе нет информации о том,"):
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


def _trim_answer_line_citations(text: str) -> str:
    """In the first line starting with 'Ответ:', keep only the first two citation markers [n]."""
    if not (text or "").strip():
        return text or ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("Ответ:"):
            matches = list(re.finditer(r"\[\d+\]", line))
            if len(matches) >= 3:
                for m in reversed(matches[2:]):
                    line = line[: m.start()] + line[m.end() :]
                lines[i] = line
            break
    return "\n".join(lines)


def ensure_doc_sections(answer_text: str, retrieved: list) -> str:
    """If answer has Ответ/Детали/Источники or Answer/Sources (EN) return as-is; else build structured text from retrieved."""
    if not (answer_text or "").strip() or not retrieved:
        return answer_text or ""
    t = (answer_text or "").strip()
    if "Ответ:" in t and "Детали:" in t and "Источники:" in t:
        return answer_text
    if "Answer:" in t and "Sources:" in t:
        return answer_text
    top = (retrieved or [])[:5]
    if any(((r or {}).get("snippet") or (r or {}).get("text") or "").strip().startswith("Шаг ") for r in top):
        top = sorted(top, key=lambda r: int(r.get("chunk_index", 0)) if isinstance(r.get("chunk_index"), (int, float)) else 0)
    snips = []
    for idx, r in enumerate(top, start=1):
        s = (r.get("snippet") or r.get("text") or "").strip()
        if s:
            snips.append((idx, s))
    if not snips:
        return answer_text or ""
    first = snips[0][1]
    dot = first.find(". ", 10)
    if dot > 0:
        answer_sent = first[: dot + 1].strip()
    else:
        words = first.split()
        answer_sent = " ".join(words[:25]) + ("..." if len(words) > 25 else "")
    detail_bullets = []
    for src_i, s in snips[:5]:
        if ":" in s:
            colon_pos = s.find(":")
            after = s[colon_pos + 1 :].strip()
            items = [x.strip() for x in after.split(",") if x.strip()]
            if len(items) >= 2:
                take = min(5, len(items))
                for item in items[:take]:
                    item = (item or "").rstrip(" .;")
                    if item:
                        detail_bullets.append(f"- {item} [{src_i}]")
                continue
        step = (s[:80] + "..." if len(s) > 80 else s).strip()
        if step:
            detail_bullets.append(f"- {step} [{src_i}]")
    if len(detail_bullets) > 5:
        detail_bullets = detail_bullets[:5]
    source_bullets = []
    for src_i, s in snips[:3]:
        short = " ".join(s.split()[:25])
        if len(s.split()) > 25:
            short += "..."
        if short:
            source_bullets.append(f"- {short} [{src_i}]")
    lines = [f"Ответ: {answer_sent}", "", "Детали:", *detail_bullets, "", "Источники:", *source_bullets]
    non_empty = [ln for ln in lines if ln.strip()]
    while len(non_empty) > 14 and detail_bullets:
        detail_bullets.pop()
        lines = [f"Ответ: {answer_sent}", "", "Детали:", *detail_bullets, "", "Источники:", *source_bullets]
        non_empty = [ln for ln in lines if ln.strip()]
    if len(non_empty) > 14 and source_bullets:
        source_bullets.pop()
        lines = [f"Ответ: {answer_sent}", "", "Детали:", *detail_bullets, "", "Источники:", *source_bullets]
    return "\n".join(lines)


def ensure_general_sections(question: str, answer_text: str) -> str:
    """Sanitize general answers: remove legacy 'no-doc' wrapper while preserving deterministic one-liners."""
    if not (answer_text or "").strip():
        return answer_text or ""

    t = (answer_text or "").strip()
    lang = _detect_lang(question)

    # Define legacy wrapper markers
    hint_ru = "Если вам нужен ответ именно по документу, задайте вопрос о конкретном фрагменте или загрузите текст, где эта тема упоминается."
    hint_en = "If you need an answer from the document, ask about a specific fragment or upload a text where this topic appears."
    header_ru = "Общий ответ вне документа:"
    header_en = "General answer (outside the document):"

    # Detect legacy wrapper markers
    has_ru_marker = header_ru in t or hint_ru in t
    has_en_marker = header_en in t or hint_en in t
    has_legacy_marker = has_ru_marker or has_en_marker

    # If NO legacy marker present: return unchanged (preserves deterministic one-liners)
    if not has_legacy_marker:
        return t

    # Legacy marker present: strip wrapper parts
    lines = [ln.strip() for ln in t.splitlines() if (ln or "").strip()]
    cleaned_lines = []
    legacy_heads = (
        "Проверка по документу:",
        "Что именно отсутствует:",
        "Общий ответ (не из документа):",
        "Как получить точный ответ по документу:",
    )

    for ln in lines:
        # Skip disclaimer lines
        if ln.startswith("В этом документе нет информации") or ln.startswith("This document does not contain information"):
            continue
        # Skip wrapper headers
        if ln == header_ru or ln == header_en or ln.startswith(header_ru) or ln.startswith(header_en):
            continue
        # Skip hint lines
        if ln == hint_ru or ln == hint_en:
            continue
        # Skip legacy headings
        if any(ln.startswith(h) for h in legacy_heads):
            continue
        # Skip legacy bullet noise
        if ln.startswith("- В документе нет достаточных фрагментов"):
            continue
        if ln.startswith("- Уточните формулировку"):
            continue
        if ln.startswith("- Можно переформулировать"):
            continue
        if ln.startswith("- Найдите в документе фрагмент"):
            continue
        if ln.startswith("- Задайте вопрос по конкретному месту"):
            continue
        if ln.startswith("- Нет релевантных фрагментов"):
            continue
        if ln.startswith("Это общий ответ, не из документа"):
            continue
        cleaned_lines.append(ln)

    # Limit to ~10 lines
    cleaned_lines = cleaned_lines[:10]
    result = "\n".join(cleaned_lines).strip()

    # Fallback if nothing remains
    if not result:
        return "Не знаю." if lang == "ru" else "I don't know."

    return result


def _validate_and_repair_doc_answer(question: str, retrieved: list, draft: str) -> tuple:
    """Validate doc answer format; if invalid, call repair_doc_answer_openai. Language-aware: EN uses Answer/Sources (optional Quotes), RU uses Ответ/Источники (optional Цитаты). Returns (answer_str, llm_used_if_repaired_or_None)."""
    d = (draft or "").strip()
    if not d:
        return (draft, None)
    lang = detect_lang(question)
    if lang == "en":
        has_answer = "Answer:" in d
        has_sources = "Sources:" in d
        if not has_answer or not has_sources:
            try:
                context = _build_doc_context(retrieved)
                out = repair_doc_answer_openai(question, context, draft)
                return ((out.get("answer") or "").strip() or draft, out.get("llm_used"))
            except Exception:
                return (draft, None)
        return (draft, None)
    # RU: require Ответ: and Источники: (optional Цитаты:). Do not require Детали:.
    if "Ответ:" not in d or "Источники:" not in d:
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
                if route_replay == "doc_rag":
                    answer_replay = _format_doc_answer(run.question or "", _strip_noise_sections(run.final_output or ""))
                    sources_replay = sanitize_sources(
                        _filter_sources_by_citations(
                            _strip_noise_sections(run.final_output or ""),
                            sources,
                            max_items=3,
                        )
                    )
                elif route_replay == "general":
                    # For deterministic/sources_only, do NOT expand into general template.
                    # Return stored deterministic output verbatim (after noise strip),
                    # so replay UX matches live execution.
                    if (answer_mode_prev or "") in ("deterministic", "sources_only"):
                        answer_replay = _strip_noise_sections(run.final_output or "")
                    else:
                        answer_replay = _strip_noise_sections(
                            ensure_general_sections(
                                run.question or "",
                                _normalize_general_output(run.final_output or "", run.question or ""),
                            )
                        )
                    sources_replay = []
                else:
                    answer_replay = _strip_noise_sections(run.final_output or "")
                    sources_replay = sanitize_sources(sources)
                resp = {
                    "run_id": run.id,
                    "answer": answer_replay,
                    "sources": sources_replay,
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

        summary_triggers = ("о чем", "про что", "кратко", "краткое содержание", "summary", "summarize", "обзор", "суть", "главное", "основная мысль", "идея", "выжимка")
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
                    input_json={"question": question, "answer_mode": answer_mode, "document_id": document_id},
                    output_json={"llm_used": llm_used, "answer_mode": answer_mode, "route": "summary", "answer_preview": (run.final_output or "")[:500]},
                    status="success",
                )
            except Exception:
                pass
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

        if document_id is not None and not retrieved:
            chunks_qs = EmbeddingChunk.objects.filter(document_id=document_id).select_related("document").order_by("chunk_index")[:top_k]
            chunks = list(chunks_qs)
            retrieved = []
            for ch in chunks:
                txt = (ch.text or "").strip()
                if not txt:
                    continue
                retrieved.append({
                    "document_id": ch.document_id,
                    "document_title": getattr(ch.document, "title", "") if getattr(ch, "document", None) else "",
                    "chunk_id": ch.id,
                    "chunk_index": getattr(ch, "chunk_index", 0),
                    "snippet": txt[:300] if len(txt) > 300 else txt,
                    "text": txt,
                    "retriever_hint": "doc_fallback",
                })
            # MVP: if user scoped to a document, keep doc mode even on weak retrieval
            retriever_used = "doc_fallback"
            if not retrieved:
                try:
                    notice = (notice + ";doc_fallback_empty") if notice else "doc_fallback_empty"
                except Exception:
                    notice = "doc_fallback_empty"

        V_THR = 0.55
        V_HARD = 0.70
        KW_THR = 4
        if retriever_used == "keyword":
            best_kw = max((float(r.get("score") or 0) for r in retrieved), default=0)
        else:
            best_kw = max((float(r.get("keyword_score") or 0) for r in retrieved), default=0)
        best_vec = max((float(r.get("vector_score") or 0) for r in retrieved), default=0)
        # Keyword evidence must be non-trivial (avoid false hits like "есть")
        kw_evidence = _has_nontrivial_kw_terms(retrieved)
        has_kw_hit = bool(kw_evidence)

        # Soft first-person intro should only help for author/identity questions, not arbitrary ones
        soft_kw_hit = bool(
            document_id is not None and retrieved and best_vec >= SOFT_VEC_DOC
            and _has_first_person_intro(retrieved) and _is_authorish_question(question)
        )
        doc_meta_intent = bool(document_id is not None and _is_doc_metadata_question(question))
        doc_title_intent = bool(document_id is not None and _is_doc_title_question(question))
        doc_title_value = ""
        if doc_title_intent:
            doc_title_value = (Document.objects.filter(id=document_id).values_list("title", flat=True).first() or "").strip()
        relevant = ((best_kw >= KW_THR) and kw_evidence) or (has_kw_hit and best_vec >= V_THR) or (document_id is None and best_vec >= V_HARD)
        max_score = max((float(r.get("final_score") or r.get("vector_score") or r.get("score") or 0) for r in retrieved), default=0)

        # Hard gate: keep NO-DOC out of doc_rag, but don't over-prune borderline DOC queries.
        # "велосипед" had max_score≈0.52, so 0.55 still routes to general.
        if max_score < 0.55 and not doc_meta_intent:
            relevant = False
        # IMPORTANT: document_id does NOT automatically grant doc_rag.
        # Invariant (CI smoke is source of truth):
        # - document_id must NOT enable doc_rag on vector-only similarity
        # - doc_rag for document-scoped queries requires keyword evidence (has_kw_hit)
        if document_id is not None and retrieved and (has_kw_hit or soft_kw_hit or doc_meta_intent):
            relevant = True
        debug_payload = {
            "best_kw": best_kw,
            "best_vec": best_vec,
            "relevant": relevant,
            "has_kw_hit": has_kw_hit,
            "soft_kw_hit": soft_kw_hit,
            "doc_meta_intent": doc_meta_intent,
            "doc_title_intent": doc_title_intent,
            "kw_evidence": kw_evidence,
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

        if doc_title_intent and doc_title_value and retrieved:
            AgentStep.objects.create(
                run=run,
                name="retrieve_context",
                input_json={"question": question, "top_k": top_k, "retriever": retriever, "document_id": document_id},
                output_json={"results": sanitize_sources(retrieved), "retriever_used": retriever_used, "route": "doc_rag", "notice": "", "debug": debug_payload},
                status="ok",
            )
            run.status = "success"
            run.final_output = doc_title_value
            run.save(update_fields=["status", "final_output"])
            try:
                if hasattr(run, "llm_used"):
                    run.llm_used = "none"
                    run.save(update_fields=["llm_used"])
            except Exception:
                pass
            try:
                AgentStep.objects.create(
                    run=run,
                    name="generate_answer",
                    input_json={"question": question, "answer_mode": answer_mode, "document_id": document_id},
                    output_json={
                        "llm_used": "none",
                        "answer_mode": answer_mode,
                        "route": "doc_rag",
                        "answer_preview": (run.final_output or "")[:500],
                    },
                    status="success",
                )
            except Exception:
                pass
            return Response({
                "run_id": run.id,
                "answer": _strip_noise_sections(run.final_output or ""),
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
                notice = _add_out_of_doc_notice("", document_id)
                if answer_mode in ("deterministic", "sources_only"):
                    general_answer = _general_answer_deterministic(question)
                    llm_used = "none"
                else:
                    out = general_answer_openai(question)
                    general_answer = out.get("answer", "")
                    # skip repair for general answers (MVP clean LLM)
                    general_answer = general_answer
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
                    "answer": (
                        _strip_noise_sections(run.final_output or "")
                        if answer_mode in ("deterministic", "sources_only")
                        else _strip_noise_sections(run.final_output or "")
                    ),
                    "sources": [],
                    "retriever_used": "general",
                    "llm_used": llm_used,
                    "answer_mode": answer_mode,
                    "route": "general",
                    "notice": notice,
                    "debug": debug_payload,
                })
            # No retrieved context and no document selected -> general answer path (language-aware)
            notice = _add_out_of_doc_notice("", document_id)
            if answer_mode in ("deterministic", "sources_only"):
                general_answer = _general_answer_deterministic(question)
                llm_used = "none"
            else:
                out = general_answer_openai(question)
                general_answer = out.get("answer", "")
                # skip repair for general answers (MVP clean LLM)
                general_answer = general_answer
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
                    output_json={
                        "llm_used": llm_used,
                        "answer_mode": answer_mode,
                        "route": "general",
                        "answer_preview": (run.final_output or "")[:500],
                    },
                    status="success",
                )
            except Exception:
                pass

            return Response({
                "run_id": run.id,
                "answer": (
                    _strip_noise_sections(run.final_output or "")
                    if answer_mode in ("deterministic", "sources_only")
                    else _strip_noise_sections(run.final_output or "")
                ),
                "sources": [],
                "retriever_used": "general",
                "llm_used": llm_used,
                "answer_mode": answer_mode,
                "route": "general",
                "notice": notice,
                "debug": debug_payload,
            })

        if not relevant:
            notice = _add_out_of_doc_notice("", document_id)
            if answer_mode in ("deterministic", "sources_only"):
                general_answer = _general_answer_deterministic(question)
                llm_used = "none"
            else:
                out = general_answer_openai(question)
                general_answer = out.get("answer", "")
                # skip repair for general answers (MVP clean LLM)
                general_answer = general_answer
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
                "answer": (
                    _strip_noise_sections(run.final_output or "")
                    if answer_mode in ("deterministic", "sources_only")
                    else _strip_noise_sections(run.final_output or "")
                ),
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
            {
                "run_id": run.id,
                "answer": _format_doc_answer(question, _strip_noise_sections(run.final_output or "")),
                "sources": sanitize_sources(
                    _filter_sources_by_citations(
                        _strip_noise_sections(run.final_output or ""),
                        retrieved,
                        max_items=3,
                    )
                ),
                "retriever_used": retriever_used,
                "llm_used": llm_used,
                "answer_mode": answer_mode,
                "route": "doc_rag",
                "notice": "",
                "debug": debug_payload,
            }
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

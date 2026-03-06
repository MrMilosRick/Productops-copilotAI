import os
import re
from typing import List, Dict, Any, Sequence, Optional

from openai import OpenAI


def claude_rag_answer(question: str, retrieved: list,
                      answer_type: str = "free_text",
                      used_indices_only: bool = True) -> dict:
    """
    Claude-based RAG answer with streaming for TTFT measurement.
    Returns: {answer, llm_used, ttft_ms, input_tokens, output_tokens, used_indices}
    """
    import anthropic
    import os
    import time

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"answer": "", "llm_used": "none", "ttft_ms": 0,
                "input_tokens": 0, "output_tokens": 0, "used_indices": [],
                "total_time_ms": 0, "time_per_output_token_ms": 0}

    _sources_instruction = (
        "\nAfter your answer, on a new line write: SOURCES: "
        "followed by comma-separated 1-based indices of context snippets "
        "you actually used (e.g. SOURCES: 1,3,5). "
        "If you used no sources write: SOURCES: none"
    )

    # Select model by answer_type
    if answer_type in ("boolean", "number", "name", "names", "date"):
        model = "claude-haiku-4-5-20251001"
    else:
        model = "claude-sonnet-4-6"

    # Build context
    ctx_lines = []
    for i, r in enumerate(retrieved[:15], start=1):
        title = (r or {}).get("document_title", "")
        text = ((r or {}).get("text") or (r or {}).get("snippet") or "")[:2000]
        ctx_lines.append(f"[{i}] {title}\n{text}")
    context = "\n\n".join(ctx_lines)

    # Build system prompt by answer_type
    if answer_type == "boolean":
        system = (
            "You are a legal RAG assistant for DIFC law documents. "
            "Answer ONLY using the provided context. "
            "Return ONLY the word Yes or No. "
            "If the answer is not in the context, return null. "
            "Your ENTIRE response must be a single word: Yes, No, or null. "
            "No explanation. No punctuation. No other text."
            + (_sources_instruction if used_indices_only else "")
        )
    elif answer_type == "number":
        system = (
            "You are a legal RAG assistant for DIFC law documents. "
            "Answer ONLY using the provided context. "
            "Return ONLY the number as a digit. "
            "If the answer is not in the context, return null. "
            "Your ENTIRE response must be a single number or null. "
            "No explanation. No units. No other text."
            + (_sources_instruction if used_indices_only else "")
        )
    elif answer_type == "name":
        system = (
            "You are a legal RAG assistant for DIFC law documents. "
            "Answer ONLY using the provided context. "
            "Return ONLY the name. No explanation, no markdown, no preamble. "
            "If the answer cannot be determined from context, return exactly: null"
            + (_sources_instruction if used_indices_only else "")
        )
    elif answer_type == "names":
        system = (
            "You are a legal RAG assistant for DIFC law documents. "
            "Answer ONLY using the provided context. "
            "Return ONLY a comma-separated list of names. No explanation, no markdown, no preamble. "
            "If the answer cannot be determined from context, return exactly: null. "
            "Do not write any sentence or explanation. "
            "Just the name(s) separated by commas. Nothing else."
            + (_sources_instruction if used_indices_only else "")
        )
    elif answer_type == "date":
        system = (
            "You are a legal RAG assistant for DIFC law documents. "
            "Answer ONLY using the provided context. "
            "Return ONLY the date in format DD Month YYYY (e.g. '15 March 2024'). "
            "No explanation, no markdown, no preamble. "
            "If the answer cannot be determined from context, return exactly: null"
            + (_sources_instruction if used_indices_only else "")
        )
    else:  # free_text
        system = (
            "You are a legal RAG assistant for DIFC law documents. "
            "Answer ONLY using the provided context snippets. "
            "Be concise and accurate. No markdown formatting. Plain text only. "
            "If the information is not in the context, respond with exactly: "
            "There is no information on this question"
            + (_sources_instruction if used_indices_only else "")
        )

    user = f"Question: {question}\n\nContext:\n{context}"

    client_a = anthropic.Anthropic(api_key=api_key)

    t0 = time.time()
    t_end = None
    ttft_ms = None
    answer_parts = []
    input_tokens = 0
    output_tokens = 0

    try:
        with client_a.messages.stream(
            model=model,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": user}]
        ) as stream:
            for text in stream.text_stream:
                if ttft_ms is None:
                    ttft_ms = round((time.time() - t0) * 1000)
                answer_parts.append(text)

            # Get usage from final message
            final_msg = stream.get_final_message()
            input_tokens = final_msg.usage.input_tokens
            output_tokens = final_msg.usage.output_tokens
            t_end = time.time()
            total_time_ms = round((t_end - t0) * 1000)
            if ttft_ms and output_tokens > 1:
                time_per_output_token_ms = round(
                    (t_end - (t0 + ttft_ms / 1000)) / (output_tokens - 1) * 1000, 2
                )
            else:
                time_per_output_token_ms = round(total_time_ms / max(output_tokens, 1), 2)

    except Exception as e:
        return {"answer": str(e), "llm_used": "error",
                "ttft_ms": 0, "input_tokens": 0, "output_tokens": 0, "used_indices": [],
                "total_time_ms": 0, "time_per_output_token_ms": 0}

    answer = "".join(answer_parts).strip()
    # Extract used source indices
    used_indices = []
    if "SOURCES:" in answer:
        parts = answer.rsplit("SOURCES:", 1)
        answer = parts[0].strip()
        src_part = parts[1].strip()
        if src_part.lower() != "none":
            for s in src_part.split(","):
                s = s.strip()
                if s.isdigit():
                    used_indices.append(int(s) - 1)  # convert to 0-based index
    # For free_text: if answer starts with the unanswerable phrase, truncate
    if answer_type == "free_text":
        unanswerable = "There is no information on this question"
        if answer.startswith(unanswerable):
            answer = unanswerable
    # Normalize null string to None
    if answer.lower().strip() in ("null", "none", "n/a", ""):
        answer = None
    # Convert number answers to int
    if answer_type == "number" and answer is not None:
        try:
            answer = int(str(answer).strip())
        except (ValueError, TypeError):
            try:
                answer = float(str(answer).strip())
            except (ValueError, TypeError):
                pass
    if ttft_ms is None:
        ttft_ms = round((time.time() - t0) * 1000)

    return {
        "answer": answer,
        "llm_used": model,
        "ttft_ms": ttft_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "used_indices": used_indices,
        "total_time_ms": total_time_ms,
        "time_per_output_token_ms": time_per_output_token_ms,
    }


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def detect_lang(text: Optional[str]) -> str:
    """Return 'ru' if text contains Cyrillic, else 'en'."""
    return "ru" if text and CYRILLIC_RE.search(text) else "en"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _openai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _strip_noise_sections(text: str) -> str:
    """
    Remove noise headings and everything after them.
    Strips: Примечания:, Дополнительно: (and any content after).
    """
    if not text:
        return ""
    noise = re.compile(
        r"(?m)^\s*(Примечания:|Дополнительно:)\s*$",
        re.IGNORECASE,
    )
    m = noise.search(text)
    if m:
        text = text[: m.start()].rstrip()
    return text.strip()


def _validate_doc_answer(text: str) -> tuple:
    """
    Validate doc_rag answer: accept RU (Ответ/Цитаты/Источники) or EN (Answer/Quotes/Sources).
    Reject noise headings. Returns (ok: bool, reason: str).
    """
    if not text or not text.strip():
        return (False, "empty")
    t = text.strip()
    noise_ru = ("Детали:", "Примечания:", "Дополнительно:", "Разбор:", "Что уточнить дальше:")
    if any(h in t for h in noise_ru):
        return (False, "noise_headings")
    has_ru = "Ответ:" in t and "Источники:" in t
    has_en = "Answer:" in t and "Sources:" in t
    if not (has_ru or has_en):
        return (False, "missing_answer_or_sources")
    return (True, "")


GENERAL_HINT = "Если вам нужен ответ именно по документу, задайте вопрос о конкретном фрагменте или загрузите текст, где эта тема упоминается."
GENERAL_HINTS = {
    "ru": "Если вам нужен ответ именно по документу, задайте вопрос о конкретном фрагменте или загрузите текст, где эта тема упоминается.",
    "en": "If you need an answer from the document, ask about a specific fragment or upload a text where this topic appears.",
}
GENERAL_HEADERS = {
    "ru": "Общий ответ вне документа:",
    "en": "General answer (outside the document):",
}
DISCLAIMER_TPL = {
    "ru": "В этом документе нет информации о {q}.",
    "en": "This document does not contain information about {q}.",
}
LEGACY_GENERAL_HEADINGS = (
    "Проверка по документу:",
    "Что именно отсутствует:",
    "Общий ответ (не из документа):",
    "Как получить точный ответ по документу:",
)

RU_BASE_POINTS = (
    "Уточните, что именно вы хотите узнать о «{q}» (факт/опыт/мнение/инструкция) — это влияет на точность ответа.",
    "Если вопрос практический — сформулируйте критерии (цель, ограничения, контекст), и тогда рекомендации будут конкретнее.",
    "Дальше можно перейти к проверке по документам/источникам, когда появится релевантный фрагмент текста.",
)
EN_BASE_POINTS = (
    "Clarify the goal and constraints (budget, timeline, requirements).",
    "Define decision criteria and compare 2–3 options against them.",
    "Validate with a small test or checklist before committing.",
)


def _build_general_template(question: str, lang: str = "en") -> str:
    q = (question or "").strip()
    ql = q.lower()
    wants_one_sentence = any(k in ql for k in ("one-sentence", "one sentence", "single sentence", "коротко", "в одном предложении", "кратко"))

    # MVP UX contract for route=general:
    # - give a direct helpful answer
    # - do NOT mention documents/sources/retrieval
    # - keep it short (1 sentence when requested)
    if (lang or "").lower().startswith("ru"):
        if wants_one_sentence:
            return (
                "Дай прямой ответ одним предложением. "
                "Не упоминай документы, источники, поиск, RAG или ограничения. "
                f"Вопрос: {q}"
            )
        return (
            "Дай прямой, практичный ответ одним коротким абзацем из 1–2 предложений. "
            "Не используй буллеты, списки или переносы строк. "
            "Не упоминай документы, источники, поиск, RAG или ограничения. "
            f"Вопрос: {q}"
        )
    if wants_one_sentence:
        return (
            "Give a direct one-sentence answer. "
            "Do not mention documents, sources, retrieval, RAG, or limitations. "
            f"Question: {q}"
        )
    return (
        "Give a direct, practical answer as one short paragraph in 1–2 sentences. "
        "Do not use bullets, numbered lists, or line breaks. "
        "Do not mention documents, sources, retrieval, RAG, or limitations. "
        f"Question: {q}"
    )


def _normalize_general_output(text: str, topic_hint: str, lang: Optional[str] = None) -> str:
    """Enforce fallback UX: disclaimer, general answer, hint (≤10 lines). Uses lang or detect_lang."""
    if lang is None:
        lang = detect_lang(topic_hint or text)
    template = _build_general_template(topic_hint, lang=lang)
    t = (text or "").strip()
    if not t:
        return template
    hint_ru = GENERAL_HINTS["ru"]
    hint_en = GENERAL_HINTS["en"]
    has_disclaimer = "В этом документе нет информации" in t or "This document does not contain information" in t
    has_hint = hint_ru in t or hint_en in t
    has_legacy = any(h in t for h in LEGACY_GENERAL_HEADINGS)
    if not has_disclaimer or not has_hint or has_legacy:
        return template
    lines = [ln.rstrip() for ln in t.splitlines() if ln.strip()]
    if len(lines) > 10:
        lines = lines[:10]
    return "\n".join(lines)


def _normalize_rag_output(text: str) -> str:
    """
    Deterministic UX normalization for RAG outputs.
    - Replace legacy heading 'Детали:' with 'Цитаты:' (RU); 'Details:' with 'Quotes:' (EN).
    """
    if not text:
        return ""
    text = re.sub(r"(?m)^\s*Детали:\s*$", "Цитаты:", text)
    text = re.sub(r"(?m)^\s*Details:\s*$", "Quotes:", text, flags=re.IGNORECASE)
    return text.strip()


def _normalize_general_chat_answer(text: str) -> str:
    """Force general-answer UX: one short paragraph, 1-2 sentences, no bullets/lists."""
    t = (text or "").strip()
    if not t:
        return ""
    parts = []
    for ln in t.splitlines():
        s = (ln or "").strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]+\s*", "", s)
        s = re.sub(r"^\d+[.)]\s*", "", s)
        s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
        parts.append(s)
    t = re.sub(r"\s{2,}", " ", " ".join(parts)).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    out = " ".join(sentences[:2]).strip() if sentences else t
    out = re.sub(r"\s{2,}", " ", out).strip()
    max_chars = 240
    if len(out) > max_chars:
        cut = out[: max_chars + 1]
        ws = cut.rfind(" ")
        if ws > 0:
            cut = cut[:ws]
        else:
            cut = out[:max_chars]
        out = cut.rstrip(" ,;:.!?") + "…"
    return out


def _extract_author_name_from_snippets(retrieved: List[Dict[str, Any]]) -> tuple:
    """
    Extract author name from retrieved snippets using first-person patterns.
    Returns (name: str or None, snippet_index: int or None) where snippet_index is 1-based.
    """
    pattern = re.compile(r"\b(?:I am|I'm|My name is)\s+([A-Z][a-z]+)\b")
    for idx, r in enumerate(retrieved, start=1):
        block = ((r or {}).get("text") or (r or {}).get("snippet") or "").strip()
        if not block:
            continue
        match = pattern.search(block)
        if match:
            return (match.group(1), idx)
    return (None, None)


def rag_answer_openai(question: str, retrieved: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns dict: { "answer": str, "llm_used": str }
    Uses Responses API.
    """
    lang = detect_lang(question)
    retrieved = (retrieved or [])[:5]
    
    # Deterministic guard for EN author-name questions
    if lang == "en":
        q_lower = (question or "").lower()
        is_author_query = any(term in q_lower for term in ("author", "name", "who", "what is your name", "what's your name"))
        if is_author_query:
            author_name, snippet_idx = _extract_author_name_from_snippets(retrieved)
            if author_name and snippet_idx:
                return {"answer": f"Answer: {author_name}. [{snippet_idx}]", "llm_used": "none"}
    
    if not _openai_available():
        answer_label = "Answer:" if lang == "en" else "Ответ:"
        sources_label = "Sources:" if lang == "en" else "Источники:"
        no_answer_ru = "Ответ: В документе нет прямого ответа на этот вопрос.\n\nИсточники:\n(нет фрагментов)"
        no_answer_en = "Answer: The document does not contain a direct answer to this question.\n\nSources:\n(no fragments)"
        parts = [answer_label]
        src_lines = []
        for i, r in enumerate(retrieved[:3], start=1):
            block = ((r or {}).get("text") or (r or {}).get("snippet") or "").strip()[:300]
            if block:
                src_lines.append(f"- {block} [{i}]")
        if src_lines:
            parts.append(" (from document, no LLM)." if lang == "en" else " По документу (без LLM).")
            parts.append("")
            parts.append(sources_label)
            parts.extend(src_lines)
            ans = "\n".join(parts)
        else:
            ans = no_answer_en if lang == "en" else no_answer_ru
        return {"answer": ans, "llm_used": "none"}
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)

    ctx_lines = []
    for i, r in enumerate(retrieved, start=1):
        title = (r or {}).get("document_title", "")
        block = ((r or {}).get("text") or (r or {}).get("snippet") or "").strip()
        block = block[:3500]  # cap per block to avoid huge prompts
        ctx_lines.append(f"[{i}] {title}\n{block}")

    context = "\n\n".join(ctx_lines).strip()

    lang_instruction = "Output in English only. " if lang == "en" else "Output in Russian only. "
    cyr_guard = "- Never output any Cyrillic characters.\n" if lang == "en" else ""
    if lang == "en":
        system = (
            f"You are a RAG assistant. {lang_instruction}Answer ONLY using the provided context snippets. "
            "Hard rules: "
            "- Do NOT add any claim that is not directly supported by the snippets. "
            "- Do NOT infer, generalize, or fill gaps. "
            "- Use ONLY information explicitly stated in the snippets. "
            f"{cyr_guard}"
            "- Never duplicate citations like '[1]. [1]' (use citations once at the end of a sentence/line). "
            "- Allowed sections ONLY: 'Answer:', optional 'Quotes:', 'Sources:'. Do NOT output any other headings. "
            "Output format (clean and readable): "
            "1) 'Answer:' — 1–2 natural sentences. Citations ONLY at the end of each sentence, like [1] or [1][2]. "
            "2) OPTIONAL 'Quotes:' — include only if needed. 1–3 lines. Each line MUST be a verbatim quote (<= 30 words) + citation [n]. "
            "3) 'Sources:' — 1–3 lines. Each line MUST be a verbatim quote (<= 30 words) + citation [n]. "
            "If the answer fits in 1–2 sentences AND is supported by a single snippet, SKIP 'Quotes:' and go straight to 'Sources:'. "
            "Author-name rule: If a snippet speaks in first person (e.g., 'I ...', 'My name is ...'), treat 'I' as the document's author/narrator. "
            "For questions about the author's name, if any snippet explicitly states 'I am <Name>' or 'My name is <Name>' (or bilingual equivalents where the English name is present), the answer MUST use that name (English only, no Cyrillic). "
            "If snippets do not contain the requested information: "
            "- Return exactly: 'Answer: The document does not contain a direct answer to this question.' ONLY when snippets do NOT explicitly state the author/narrator name (e.g., no 'I am <Name>' or 'My name is <Name>'). "
            "- Then 'Sources:' empty, OR at most 1 closest snippet (verbatim quote + citation). "
            "Never mention anything outside the snippets."
        )
    else:
        system = (
            f"You are a RAG assistant. {lang_instruction}Answer ONLY using the provided context snippets. "
            "Hard rules: "
            "- Do NOT add any claim that is not directly supported by the snippets. "
            "- Do NOT infer, generalize, or fill gaps. "
            "- Use ONLY information explicitly stated in the snippets. "
            "- Never duplicate citations like '[1]. [1]' (use citations once at the end of a sentence/line). "
            "- Allowed sections ONLY: 'Ответ:', optional 'Цитаты:', 'Источники:'. Do NOT output any other headings. "
            "- Forbidden heading: 'Детали:' (never use it). "
            "Output format (clean and readable): "
            "1) 'Ответ:' — 1–2 natural sentences. Citations ONLY at the end of each sentence, like [1] or [1][2]. "
            "2) OPTIONAL 'Цитаты:' — include only if needed. 1–3 lines. Each line MUST be a verbatim quote (<= 30 words) + citation [n]. "
            "3) 'Источники:' — 1–3 lines. Each line MUST be a verbatim quote (<= 30 words) + citation [n]. "
            "If the answer fits in 1–2 sentences AND is supported by a single snippet, SKIP 'Цитаты:' and go straight to 'Источники:'. "
            "If snippets do not contain the requested information: "
            "- Return exactly: 'Ответ: В документе нет прямого ответа на этот вопрос.' "
            "- Then 'Источники:' empty, OR at most 1 closest snippet (verbatim quote + citation). "
            "Never mention anything outside the snippets."
        )

    user = f"Question:\n{question}\n\nContext:\n{context}"

    client = OpenAI()
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        reasoning={"effort": effort},
        max_output_tokens=max_out,
    )

    ans = _normalize_rag_output(resp.output_text or "")
    ans = _strip_noise_sections(ans)
    ok, _ = _validate_doc_answer(ans)

    # Guard: EN question must produce EN answer (no Cyrillic). If model slips into RU, do a strict rewrite.
    if lang == "en" and CYRILLIC_RE.search(ans or ""):
        try:
            repair_system = (
                "Rewrite the text into English ONLY. "
                "Hard rules: "
                "- Do NOT add any new facts. "
                "- Preserve meaning strictly. "
                "- Output 1–2 natural sentences only. "
                "- Never output any Cyrillic characters. "
                "- Do NOT add headings, quotes, or citations."
            )
            repair_user = f"Text to rewrite:\n{ans}"
            repair_resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": repair_system},
                    {"role": "user", "content": repair_user},
                ],
                reasoning={"effort": effort},
                max_output_tokens=max_out,
            )
            repaired = (repair_resp.output_text or "").strip()
            if repaired and not CYRILLIC_RE.search(repaired):
                ans = repaired
        except Exception:
            pass

    if not ok:
        return general_answer_openai(question)
    return {"answer": ans, "llm_used": model}


def general_answer_openai(question: str) -> Dict[str, Any]:
    """
    General answer (no RAG context). Same env vars as rag_answer_openai.
    Returns dict: { "answer": str, "llm_used": str }
    """
    topic = (question or "").strip() or "заданной теме"
    lang = detect_lang(question)
    if not _openai_available():
        return {"answer": _build_general_template(topic, lang=lang), "llm_used": "none"}

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)
    if lang == "en":
        system = (
            "You are a helpful assistant. "
            "Give a direct, practical answer to the user's question. "
            "Output one short paragraph in 1-2 sentences only. "
            "Do not use bullets, numbered lists, or line breaks. "
            "Do NOT mention documents, sources, retrieval, RAG, or limitations. "
            "Keep the answer concise."
        )
    else:
        system = (
            "Ты полезный ассистент. "
            "Дай прямой, практичный ответ на вопрос пользователя. "
            "Выводи один короткий абзац из 1-2 предложений. "
            "Не используй буллеты, нумерованные списки или переносы строк. "
            "Не упоминай документы, источники, поиск или ограничения. "
            "Ответ должен быть кратким."
        )
    client = OpenAI()
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": topic},
        ],
        reasoning={"effort": effort},
        max_output_tokens=max_out,
    )
    ans = _normalize_general_chat_answer((resp.output_text or "").strip())
    return {"answer": ans, "llm_used": model}


def repair_fallback_openai(question: str, draft: str) -> Dict[str, Any]:
    """
    Rewrite draft into fallback template (RU or EN by question language). Same env vars as general_answer_openai.
    Returns dict: { "answer": str, "llm_used": str }
    """
    lang = detect_lang(question)
    topic = (question or "").strip() or ("заданной теме" if lang == "ru" else "your question")
    if not _openai_available():
        if (draft or "").strip():
            return {"answer": (draft or "").strip(), "llm_used": "none"}
        return {"answer": _build_general_template(topic, lang=lang), "llm_used": "none"}
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)
    hint = GENERAL_HINTS.get(lang, GENERAL_HINT)
    if lang == "en":
        system = (
            "Rewrite the draft into the fallback format. Output in English only, <=8 non-empty lines.\n"
            "1) Line 1: 'This document does not contain information about <question>.'\n"
            "2) Line 2 starts with: 'General answer (outside the document):'\n"
            "3) Lines 3-5: 2-4 short bullet lines (<=20 words)\n"
            f"4) Last line MUST be exactly: '{hint}'\n"
            "Remove legacy headings/boilerplate. No document snippets. No fabricated citations.\n"
        )
    else:
        system = (
            "Rewrite the draft into the fallback format. Output in Russian only, <=8 non-empty lines.\n"
            "1) Line 1: 'В этом документе нет информации о <question>.'\n"
            "2) Line 2 starts with: 'Общий ответ вне документа:'\n"
            "3) Lines 3-5: 2-4 short bullet lines (<=20 words)\n"
            f"4) Last line MUST be exactly: '{hint}'\n"
            "Remove legacy headings/boilerplate. No document snippets. No fabricated citations.\n"
        )
    user = f"Question:\n{question}\n\nDraft to rewrite:\n{draft}"
    client = OpenAI()
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        reasoning={"effort": effort},
        max_output_tokens=max_out,
    )
    ans = _normalize_general_output((resp.output_text or "").strip(), topic, lang=lang)
    return {"answer": ans, "llm_used": model}


def repair_doc_answer_openai(question: str, context: str, draft: str) -> Dict[str, Any]:
    """
    Rewrite draft into strict doc-answer format. EN: Answer/Quotes/Sources. RU: Ответ/Цитаты/Источники. Preserve citation indices [1]..
    Returns dict: { "answer": str, "llm_used": str }
    """
    lang = detect_lang(question)
    if not _openai_available():
        if (draft or "").strip():
            return {"answer": (draft or "").strip(), "llm_used": "none"}
        if lang == "en":
            parts = ["Answer: The document does not contain a direct answer to this question.", "", "Sources:"]
        else:
            parts = ["Ответ: В документе нет прямого ответа на этот вопрос.", "", "Источники:"]
        for i, block in enumerate((context or "").split("\n\n")[:3], start=1):
            line = block.strip().split("\n", 1)[-1].strip()[:200] if block.strip() else ""
            if line:
                parts.append(f"- {line} [{i}]")
        if len(parts) == 3:
            parts.append("(no fragments)" if lang == "en" else "(нет фрагментов)")
        return {"answer": "\n".join(parts), "llm_used": "none"}
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)
    lang_instruction = "Output in English only. " if lang == "en" else "Output in Russian only. "
    if lang == "en":
        system = (
            "Rewrite the draft into the strict RAG format. " + lang_instruction +
            "Format strictly: (1) Answer: 1–2 sentences; each sentence must end with citations like [1] or [1][2]. "
            "(2) Optional Quotes: 1–3 lines; each line = short quote (≤30 words) verbatim from context + citation [n]. "
            "(3) Sources: 2–5 items; each = short quote (≤30 words) verbatim from context + citation [n]. "
            "Preserve citation indices [1], [2], ... Never output Cyrillic. "
            "If the context does not contain the answer, output 'Answer: The document does not contain a direct answer to this question.' and Sources: empty or at most 1 snippet with citation."
        )
    else:
        system = (
            "Rewrite the draft into the strict RAG format. " + lang_instruction +
            "Format strictly: (1) Ответ: 1–2 sentences; each sentence must end with citations like [1] or [1][2]. "
            "(2) Optional Цитаты: 1–3 lines; each line = short quote (≤30 words) verbatim from context + citation [n]. "
            "(3) Источники: 2–5 items; each = short quote (≤30 words) verbatim from context + citation [n]. "
            "Preserve citation indices [1], [2], ... "
            "If the context does not contain the answer, output 'Ответ: В документе нет прямого ответа на этот вопрос.' and Источники: empty or at most 1 snippet with citation."
        )
    user = f"Question:\n{question}\n\nContext:\n{context}\n\nDraft to rewrite:\n{draft}"
    client = OpenAI()
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        reasoning={"effort": effort},
        max_output_tokens=max_out,
    )
    return {"answer": resp.output_text or "", "llm_used": model}


# Back-compat alias: some code imports rag_answer_langchain
def rag_answer_langchain(question, retrieved):
    # If you already have rag_answer_openai, reuse it
    return rag_answer_openai(question, retrieved)

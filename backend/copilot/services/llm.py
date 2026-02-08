import os
import re
from typing import List, Dict, Any

from openai import OpenAI


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
    Strips: Детали:, Примечания:, Дополнительно: (and any content after).
    """
    if not text:
        return ""
    noise = re.compile(
        r"(?m)^\s*(Детали:|Примечания:|Дополнительно:)\s*$",
        re.IGNORECASE,
    )
    m = noise.search(text)
    if m:
        text = text[: m.start()].rstrip()
    return text.strip()


def _validate_doc_answer(text: str) -> tuple:
    """
    Validate DOC-ANSWER per DoD. Text must already be stripped of noise (Детали:, etc.).
    Required: Ответ:, Разбор:, Источники:, Что уточнить дальше:
    Разбор: 3–7 bullets (- ). Источники: 2–5 lines with locator (стр./абз./секция/таймкод). Что уточнить: 1–3 bullets.
    Returns (ok: bool, reason: str).
    """
    if not text or not text.strip():
        return (False, "empty")
    t = text.strip()
    if "Детали:" in t or "Примечания:" in t or "Дополнительно:" in t:
        return (False, "noise_headings")
    for sec in ("Ответ:", "Разбор:", "Источники:", "Что уточнить дальше:"):
        if sec not in t:
            return (False, f"missing_{sec.replace(' ', '_')[:-1]}")
    idx_razbor = t.find("Разбор:")
    idx_ist = t.find("Источники:")
    idx_chto = t.find("Что уточнить дальше:")
    if idx_razbor < 0 or idx_ist < 0 or idx_chto < 0:
        return (False, "section_order")
    razbor_block = t[idx_razbor:idx_ist]
    bullets_razbor = [ln for ln in razbor_block.splitlines() if ln.strip().startswith("- ")]
    if len(bullets_razbor) < 3 or len(bullets_razbor) > 7:
        return (False, "разбор_bullets")
    ist_block = t[idx_ist:idx_chto]
    locator_re = re.compile(r"\(.*(?:стр\.|абз\.|секция|таймкод)")
    def _is_source_line(ln: str) -> bool:
        s = ln.strip()
        if len(s) < 3 or s[:2] != "- " or not locator_re.search(ln):
            return False
        return s[2] in ('"', '\u201c', '«')  # " or " or «
    source_lines = [ln for ln in ist_block.splitlines() if _is_source_line(ln)]
    if len(source_lines) < 2 or len(source_lines) > 5:
        return (False, "источники_count")
    chto_block = t[idx_chto:]
    bullets_chto = [ln for ln in chto_block.splitlines() if ln.strip().startswith("- ")]
    if len(bullets_chto) < 1 or len(bullets_chto) > 3:
        return (False, "уточнить_bullets")
    return (True, "")


def _normalize_general_output(text: str, topic_hint: str) -> str:
    """
    Force FALLBACK template per DoD. Exact headings:
    Проверка по документу:, Что именно отсутствует:, Общий ответ (не из документа):, Как получить точный ответ по документу:
    3–6 bullets in general section, 1–2 in Что именно отсутствует, 1–3 in last section. Trim <=1500 chars.
    """
    if not text:
        text = ""
    t = text.strip()
    required_headers = (
        "Проверка по документу:",
        "Что именно отсутствует:",
        "Общий ответ (не из документа):",
        "Как получить точный ответ по документу:",
    )
    for h in required_headers:
        if h not in t:
            t = (
                f"Проверка по документу: В документе нет информации о {topic_hint}.\n\n"
                f"Что именно отсутствует:\n"
                f"- В документе нет достаточных фрагментов о: {topic_hint}.\n\n"
                f"Общий ответ (не из документа):\n"
                f"Это общий ответ, не из документа.\n"
                f"- Уточните формулировку или загрузите документ с нужной темой.\n"
                f"- Можно переформулировать вопрос.\n\n"
                f"Как получить точный ответ по документу:\n"
                f"- Найдите в документе фрагмент с упоминанием темы.\n"
                f"- Задайте вопрос по конкретному месту в документе."
            )
            break
    if "Общий ответ (не из документа):" in t and "Это общий ответ, не из документа." not in t:
        t = t.replace(
            "Общий ответ (не из документа):",
            "Общий ответ (не из документа):\nЭто общий ответ, не из документа.",
            1,
        )
    if len(t) > 1500:
        t = t[:1500].rstrip()
    return t


def _normalize_rag_output(text: str) -> str:
    """
    Deterministic UX normalization for RAG outputs.
    - Replace legacy heading 'Детали:' with 'Цитаты:' (same meaning in our UX).
    - Keep everything else as-is to avoid changing semantics.
    """
    if not text:
        return ""
    text = re.sub(r"(?m)^\s*Детали:\s*$", "Цитаты:", text)
    return text.strip()


def rag_answer_openai(question: str, retrieved: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns dict: { "answer": str, "llm_used": str }
    Uses Responses API.
    """
    retrieved = (retrieved or [])[:5]
    if not _openai_available():
        parts = ["Ответ:"]
        src_lines = []
        for i, r in enumerate(retrieved[:3], start=1):
            block = ((r or {}).get("text") or (r or {}).get("snippet") or "").strip()[:300]
            if block:
                src_lines.append(f"- {block} [{i}]")
        if src_lines:
            parts.append(" По документу (без LLM).")
            parts.append("")
            parts.append("Источники:")
            parts.extend(src_lines)
            ans = "\n".join(parts)
        else:
            ans = "Ответ: В документе нет прямого ответа на этот вопрос.\n\nИсточники:\n(нет фрагментов)"
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

    system = (
        "You are a RAG assistant. Output in Russian only. Answer ONLY using the provided context snippets. "
        "Hard rules: "
        "- Do NOT add any claim that is not directly supported by the snippets. "
        "- Do NOT infer, generalize, or fill gaps. "
        "- Use ONLY information explicitly stated in the snippets. "
        "- Never duplicate citations like '[1]. [1]' (use citations once at the end of a sentence/line). "
        "- Allowed sections ONLY: 'Ответ:', optional 'Цитаты:', 'Источники:'. Do NOT output any other headings. "
        "- Forbidden heading: 'Детали:' (never use it). "
        "Output format (clean and readable): "
        "1) 'Ответ:' — 1–2 natural sentences. Citations ONLY at the end of each sentence, like [1] or [1][2]. "
        "2) OPTIONAL 'Цитаты:' — include only if needed. "
        "   1–3 lines. Each line MUST be a verbatim quote (<= 30 words) + citation [n]. "
        "3) 'Источники:' — 1–3 lines. Each line MUST be a verbatim quote (<= 30 words) + citation [n]. "
        "Rules for when to include 'Цитаты:': "
        "- If the answer fits in 1–2 sentences AND is supported by a single snippet, SKIP 'Цитаты:' and go straight to 'Источники:'. "
        "- If multiple snippets are used OR the question is nuanced, include 'Цитаты:' (1–3 lines). "
        "If snippets do not contain the requested information: "
        "- Return exactly: 'Ответ: В документе нет прямого ответа на этот вопрос.' "
        "- Then return 'Источники:' empty, OR include at most 1 closest snippet ONLY if it partially mentions the asked concept (verbatim quote + citation). "
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
    if not ok:
        q = (question or "").strip() or "заданную тему"
        ans = _normalize_general_output(
            f"В документе недостаточно доказательств для ответа на вопрос: {q}.",
            q,
        )
    return {"answer": ans, "llm_used": model}


def general_answer_openai(question: str) -> Dict[str, Any]:
    """
    General answer (no RAG context). Same env vars as rag_answer_openai.
    Returns dict: { "answer": str, "llm_used": str }
    """
    if not _openai_available():
        q = (question or "").strip() or "заданный вопрос"
        return {
            "answer": (
                f"Проверка по документу: В документе нет информации для ответа на: {q}.\n\n"
                "Что именно отсутствует:\n- В документе нет достаточных фрагментов по запросу.\n\n"
                "Общий ответ (не из документа):\n"
                "- Уточните формулировку или загрузите документ с нужной темой.\n"
                "- Можно переформулировать вопрос.\n\n"
                "Как получить точный ответ по документу:\n"
                "- Найдите в документе фрагмент с упоминанием темы.\n"
                "- Задайте вопрос по конкретному месту в документе."
            ),
            "llm_used": "none",
        }
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)
    system = (
        "You are a fallback assistant when the document has no direct answer. Output in Russian, 10–14 lines max. "
        "The very first line MUST be exactly: 'В данном документе нет информации, чтобы ответить на: <question>.' Do NOT add any other notice or similar sentence. "
        "Then: (1) 'Почему нельзя точно:' — 1–2 bullets, no questions to the user. "
        "(2) 'Общий ответ вне документа:' — 3–5 bullets, concise, no mega-guides, no generic filler. "
        "(3) 'Чтобы ответить по документу:' — 2–3 concrete bullets (name/character/page/quote). "
        "Do not use modal speculation words: 'возможно', 'может быть', 'иногда'. "
        "Special rule: if the question contains 'главн' and ('героин' or 'герой' or 'персонаж'), the 'Общий ответ вне документа' bullets must be about how authors typically reveal a main character (motivation, conflict, arc, choices), and MUST NOT mention medical or diagnosis speculation."
    )
    client = OpenAI()
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        reasoning={"effort": effort},
        max_output_tokens=max_out,
    )
    ans = _normalize_general_output(resp.output_text or "", question or "")
    return {"answer": ans, "llm_used": model}


def repair_fallback_openai(question: str, draft: str) -> Dict[str, Any]:
    """
    Rewrite draft into strict Russian fallback template. Same env vars as general_answer_openai.
    Returns dict: { "answer": str, "llm_used": str }
    """
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)
    system = (
        "Rewrite the draft into a strict Russian fallback answer. 10–14 lines max. "
        "First line EXACT: 'В данном документе нет информации, чтобы ответить на: <question>.' (replace <question> with the user question). "
        "Sections in order: 'Почему нельзя точно:' (1–2 bullets); 'Общий ответ вне документа:' (3–5 bullets, concise); 'Чтобы ответить по документу:' (2–3 concrete bullets). "
        "Must NOT include 'В данном документе нет прямого ответа'. Must NOT use: возможно, может быть, возможен, иногда. "
        "If question contains 'главн' and ('героин' or 'герой' or 'персонаж'), general bullets = motivation/conflict/arc/choices only; MUST NOT mention medical/diagnosis/addiction."
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
    return {"answer": resp.output_text or "", "llm_used": model}


def repair_doc_answer_openai(question: str, context: str, draft: str) -> Dict[str, Any]:
    """
    Rewrite draft into strict Russian doc-answer format. Preserve citation indices [1].. from context blocks.
    Returns dict: { "answer": str, "llm_used": str }
    """
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)
    system = (
        "Rewrite the draft into the strict Russian RAG format. Output in Russian only. "
        "Format strictly: (1) Ответ: 1–2 sentences; each sentence must end with citations like [1] or [1][2]. "
        "(2) Детали: 3–6 bullets; every bullet must include a short quote (≤30 words) copied verbatim from the provided context and must end with citations [1], [2], etc. "
        "(3) Источники: 2–5 items; each item = short quote (≤30 words) copied verbatim from context + citation [n]. "
        "Preserve citation indices [1], [2], ... to match the context blocks. Copy quotes verbatim from the context. "
        "No claims beyond the context. If the context does not contain the answer, output one sentence 'В документе нет прямого ответа на этот вопрос.' and Источники: empty or at most 1 closest snippet quote with citation."
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

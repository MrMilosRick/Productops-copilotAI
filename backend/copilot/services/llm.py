import os
from typing import List, Dict, Any

from openai import OpenAI


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def rag_answer_openai(question: str, retrieved: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns dict: { "answer": str, "llm_used": str }
    Uses Responses API.
    """
    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low")
    max_out = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 300)

    # guardrails
    retrieved = (retrieved or [])[:5]
    ctx_lines = []
    for i, r in enumerate(retrieved, start=1):
        title = (r or {}).get("document_title", "")
        snip = ((r or {}).get("snippet", "") or "").strip()
        snip = snip[:800]  # hard cap per snippet
        ctx_lines.append(f"[{i}] {title}\n{snip}")

    context = "\n\n".join(ctx_lines).strip()

    system = (
        "You are a RAG assistant. Answer ONLY using the provided context. "
        "If context is insufficient, say you don't know. "
        "Cite sources as [1], [2] corresponding to the context blocks."
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

    return {"answer": resp.output_text or "", "llm_used": model}

# Back-compat alias: some code imports rag_answer_langchain
def rag_answer_langchain(question, retrieved):
    # If you already have rag_answer_openai, reuse it
    return rag_answer_openai(question, retrieved)

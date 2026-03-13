"""
ARLC pipeline configuration (env and defaults).

Target metrics:
- Final Score > 0.80
- SLM Metrics > 0.95
- LLM Judge Score > 0.70
- Average TTFT < 1500 ms
- TTFT Bonus > 1.05 (avg TTFT < 1.0 s)
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

# Submission format version (e.g. v14 = retrieved_chunk_ids as doc_slug_page, 1-based, only used pages)
SUBMISSION_VERSION = "v14"


def normalize_page_id(doc_slug: str, page_num: int) -> str:
    """
    Build competition-compliant page ID: doc_slug_page (1-based).
    Slug is sanitized: no path chars, no spaces (underscore), alphanumeric + underscore only.
    """
    slug = (doc_slug or "").strip()
    slug = re.sub(r"[^\w\-.]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_") or "doc"
    page = max(1, int(page_num) if isinstance(page_num, (int, float)) else 1)
    return f"{slug}_{page}"


@dataclass
class ARLCConfig:
    # Retrieval
    retrieval_top_k_candidates: int = 50
    retrieval_top_k_after_rerank: int = 5
    use_reranker: bool = True
    use_hybrid: bool = True

    # Chunking (legal)
    chunk_max_tokens: int = 450
    chunk_overlap_tokens: int = 50

    # Generation
    max_output_tokens: int = 300
    free_text_max_chars: int = 280
    streaming: bool = True  # for TTFT measurement

    # Telemetry
    require_telemetry: bool = True

    # API (for EvaluationClient)
    platform_base_url: str = "https://platform.agentic-challenge.ai"
    api_key_env: str = "ARLC_API_KEY"

    @classmethod
    def from_env(cls) -> "ARLCConfig":
        return cls(
            retrieval_top_k_candidates=int(os.getenv("ARLC_TOP_K_CANDIDATES", "50")),
            retrieval_top_k_after_rerank=int(os.getenv("ARLC_TOP_K_FINAL", "5")),
            use_reranker=os.getenv("ARLC_USE_RERANKER", "true").lower() in ("true", "1", "yes"),
            use_hybrid=os.getenv("ARLC_USE_HYBRID", "true").lower() in ("true", "1", "yes"),
            chunk_max_tokens=int(os.getenv("ARLC_CHUNK_MAX_TOKENS", "450")),
            chunk_overlap_tokens=int(os.getenv("ARLC_CHUNK_OVERLAP_TOKENS", "50")),
            max_output_tokens=int(os.getenv("ARLC_MAX_OUTPUT_TOKENS", "300")),
            free_text_max_chars=int(os.getenv("ARLC_FREE_TEXT_MAX_CHARS", "280")),
            streaming=os.getenv("ARLC_STREAMING", "true").lower() in ("true", "1", "yes"),
            require_telemetry=True,
            platform_base_url=os.getenv("ARLC_PLATFORM_URL", "https://platform.agentic-challenge.ai"),
            api_key_env="ARLC_API_KEY",
        )

    def get_api_key(self) -> Optional[str]:
        return os.getenv(self.api_key_env, "").strip() or None

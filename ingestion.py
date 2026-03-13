"""
Legal RAG - Ingestion Script
Phase 1: PDF → page-level chunks → chunk_id = {sha256}_{page}

Usage:
    pip install pymupdf
    python ingestion.py --dataset-path "/Users/maxfinch/Downloads/dataset_documents (1)/"
    python ingestion.py --dataset-path "/Users/maxfinch/Downloads/dataset_documents (1)/" --dry-run
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional

try:
    import fitz  # pymupdf
except ImportError:
    print("ERROR: pymupdf not installed. Run: pip install pymupdf")
    sys.exit(1)


# ── Constants ──────────────────────────────────────────────────────────────────

MIN_CHARS = 50  # skip pages with less content (blank/scan)

COURT_CASE_PATTERN = re.compile(
    r'\b(CFI|CA|ARB|SCT|TCD|ENF|DEC)\s*(\d{3}/\d{4})\b', re.IGNORECASE
)

LAW_PATTERN = re.compile(
    r'(DIFC\s+)?Law\s+No[..\s(]+(\d+)[).\s]+of\s+(\d{4})', re.IGNORECASE
)

REGULATIONS_PATTERN = re.compile(r"\b(Regulations|Rules)\b", re.IGNORECASE)
IN_FORCE_PATTERN = re.compile(r"\bIn\s+force\s+on\b", re.IGNORECASE)
DIFC_ANCHOR_PATTERN = re.compile(r"\bDIFC(A)?\b", re.IGNORECASE)

WS_RE = re.compile(r"[ \t]+")
NL_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    t = (text or "").replace("\x00", " ")
    t = WS_RE.sub(" ", t)
    t = NL_RE.sub("\n\n", t)
    return t.strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_file_id(path: str) -> str:
    """Use filename (already a SHA256 hash) as document ID."""
    return Path(path).stem


def classify_doc(filename: str, first_page_text: str) -> Dict[str, Optional[str]]:
    """Classify document as COURT_CASE | LAW | ENACTMENT_NOTICE and extract metadata."""
    text = first_page_text[:2000]

    # Enactment notice = tiny file, typically 1 page
    if "enactment notice" in text.lower():
        return {"doc_type": "ENACTMENT_NOTICE", "case_ref": None, "law_name": None}

    # Regulations
    if any(word in text.lower() for word in
           ['regulations', 'regulation', ' reg ']):
        return {"doc_type": "REGULATIONS",
                "case_ref": None, "law_name": None}

    # Court case
    match = COURT_CASE_PATTERN.search(text)
    if match:
        case_ref = f"{match.group(1).upper()} {match.group(2)}"
        return {"doc_type": "COURT_CASE", "case_ref": case_ref, "law_name": None}

    # Law
    law_match = LAW_PATTERN.search(text)
    if law_match:
        law_name = text[:100].strip().replace("\n", " ")
        return {"doc_type": "LAW", "case_ref": None, "law_name": law_name[:120]}

    # Regulations / Rules (common DIFC/DIFCA docs that aren't "Law No. X of YYYY")
    # Example: "LEASING REGULATIONS ... CONSOLIDATED VERSION ... In force on <date>"
    if REGULATIONS_PATTERN.search(text) and (DIFC_ANCHOR_PATTERN.search(text) or IN_FORCE_PATTERN.search(text)):
        return {"doc_type": "REGULATIONS", "case_ref": None, "law_name": None}

    return {"doc_type": "UNKNOWN", "case_ref": None, "law_name": None}


def parse_pdf(path: str) -> List[Dict[str, Any]]:
    """Parse PDF → list of page dicts using pymupdf."""
    chunks = []
    try:
        doc = fitz.open(path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            raw = page.get_text("text") or ""
            text = normalize_text(raw)
            char_count = len(text)
            chunks.append({
                "page_num": page_num + 1,  # 1-based
                "text": text,
                "char_count": char_count,
            })
        doc.close()
    except Exception as e:
        print(f"  ERROR parsing {path}: {e}")
    return chunks


# ── Main Pipeline ──────────────────────────────────────────────────────────────

def run_ingestion(dataset_path: str, dry_run: bool = False, max_files: int = 0, output: Optional[str] = None):
    dataset_dir = Path(dataset_path)
    if not dataset_dir.exists():
        print(f"ERROR: Path not found: {dataset_path}")
        sys.exit(1)

    # 1. Scan all PDFs
    pdf_files = sorted(dataset_dir.rglob("*.pdf"))
    if max_files and max_files > 0:
        pdf_files = pdf_files[:max_files]

    print(f"\n{'='*60}")
    print(f"LEGAL RAG INGESTION — {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}")
    print(f"Found {len(pdf_files)} PDF files in {dataset_path}\n")

    # 2. Deduplicate by SHA256
    hash_to_path = {}
    duplicates = []
    for pdf_path in pdf_files:
        h = get_file_id(str(pdf_path))
        if h in hash_to_path:
            duplicates.append((str(pdf_path), hash_to_path[h]))
        else:
            hash_to_path[h] = str(pdf_path)

    print(f"After deduplication: {len(hash_to_path)} unique files ({len(duplicates)} duplicates skipped)")
    if duplicates:
        print("  Duplicates:")
        for dup, original in duplicates:
            print(f"    SKIP {Path(dup).name} (same as {Path(original).name})")

    # 3. Process unique files
    print(f"\n{'─'*60}")
    all_chunks: List[Dict[str, Any]] = []
    stats = defaultdict(int)
    total_chunks_count = 0

    for file_hash, pdf_path in sorted(hash_to_path.items()):
        pdf_name = Path(pdf_path).name
        print(f"\n📄 {pdf_name}")
        print(f"   hash: {file_hash[:16]}...")

        pages = parse_pdf(pdf_path)
        if not pages:
            print("   SKIP: no pages extracted")
            continue

        # Classify using first page text
        first_text = pages[0]["text"] if pages else ""
        meta = classify_doc(pdf_name, first_text)
        doc_type = meta["doc_type"]
        stats[doc_type] += 1

        print(f"   type: {doc_type}", end="")
        if meta["case_ref"]:
            print(f" | case: {meta['case_ref']}", end="")
        if meta["law_name"]:
            print(f" | law: {meta['law_name'][:60]}", end="")
        print(f"\n   pages: {len(pages)}")

        # Build chunks
        file_chunks = []
        skipped = 0
        for page in pages:
            if page["char_count"] < MIN_CHARS:
                skipped += 1
                continue

            chunk_id = f"{file_hash}_{page['page_num']}"
            preview = (page["text"] or "")[:100].replace("\n", " ")

            chunk = {
                "chunk_id": chunk_id,
                "doc_hash": file_hash,
                "page_num": page["page_num"],
                "doc_type": doc_type,
                "case_ref": meta["case_ref"],
                "law_name": meta["law_name"],
                "char_count": page["char_count"],
                "text_preview": preview,
                "text": page["text"],
            }
            file_chunks.append(chunk)

            # Print first 3 chunks per file as sample
            if len(file_chunks) <= 3:
                print(f"   [{chunk_id}]")
                print(f"     chars={page['char_count']} | {preview!r}")

        if skipped:
            print(f"   (skipped {skipped} near-empty pages)")

        print(f"   → {len(file_chunks)} chunks")
        total_chunks_count += len(file_chunks)
        if not dry_run:
            all_chunks.extend(file_chunks)

    # 4. Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique PDFs:  {len(hash_to_path)}")
    print(f"Total chunks:       {total_chunks_count if dry_run else len(all_chunks)}")
    print(f"By doc_type:")
    for dtype, count in sorted(stats.items()):
        print(f"  {dtype:<20} {count} files")

    # 5. Save manifest (without full text to keep it readable)
    if not dry_run:
        manifest = []
        for c in all_chunks:
            manifest.append({k: v for k, v in c.items() if k != "text"})

        if output:
            out_path = Path(output)
        else:
            out_path = dataset_dir / "chunks_manifest.json"
        with open(out_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"\nManifest saved: {out_path}")
        print(f"Total chunks in manifest: {len(manifest)}")
    else:
        print(f"\nDRY RUN — nothing written to disk")

    return all_chunks


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legal RAG PDF Ingestion")
    parser.add_argument(
        "--dataset-path",
        default="/Users/maxfinch/Downloads/dataset_documents (1)/",
        help="Path to folder with PDF files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print only, don't write manifest"
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit number of PDFs for quick testing (0 = no limit)"
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    run_ingestion(args.dataset_path, dry_run=args.dry_run, max_files=args.max_files, output=args.output)

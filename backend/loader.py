#!/usr/bin/env python3
"""
Load chunks_manifest.json into Django copilot_document and copilot_embeddingchunk tables.
Run from backend/ with: python loader.py --manifest /app/data/chunks_manifest.json
"""
import json
import os
import sys

import fitz

# Ensure backend is on path and Django is configured before importing copilot
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

import django
django.setup()

import argparse
from pathlib import Path

from copilot.models import Document, EmbeddingChunk, Workspace
from copilot.services.embeddings import embed_texts


def get_page_text(pdf_dir: str, doc_hash: str, page_num: int) -> str:
    pdf_path = os.path.join(pdf_dir, doc_hash + ".pdf")
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]  # page_num is 1-based
        return page.get_text("text").replace("\x00", "")[:8000]
    except Exception as e:
        print(f"  WARN: could not read {pdf_path} page {page_num}: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(description="Load chunks manifest into Django")
    parser.add_argument("--manifest", default="/app/data/chunks_manifest.json", help="Path to chunks_manifest.json")
    parser.add_argument("--pdf-dir", default="/app/data/dataset/", help="Directory containing PDFs (filename = doc_hash.pdf)")
    parser.add_argument("--batch-size", type=int, default=50, help="Chunk processing batch size")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"ERROR: Manifest not found: {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if not manifest:
        print("Manifest is empty.")
        return

    batch_size = max(1, args.batch_size)
    ws, _ = Workspace.objects.get_or_create(name="Default", defaults={})

    # Group chunks by doc_hash
    by_doc = {}
    for c in manifest:
        h = c.get("doc_hash") or ""
        if h not in by_doc:
            by_doc[h] = []
        by_doc[h].append(c)

    # Create or get Documents per doc_hash
    doc_by_hash = {}
    for doc_hash, chunks in by_doc.items():
        first = chunks[0]
        title = (first.get("law_name") or first.get("case_ref") or doc_hash[:16])
        if isinstance(title, str):
            title = title.strip()[:255]
        else:
            title = str(title)[:255]
        filename = f"{doc_hash}.pdf"
        doc, _ = Document.objects.get_or_create(
            workspace=ws,
            content_hash=doc_hash,
            defaults={
                "title": title or doc_hash[:16],
                "filename": filename,
                "content": "",
                "status": "ready",
                "chunk_count": len(chunks),
            },
        )
        doc.chunk_count = len(chunks)
        doc.save(update_fields=["chunk_count"])
        doc_by_hash[doc_hash] = doc

    total = len(manifest)
    loaded = 0
    skipped = 0

    for i in range(0, total, batch_size):
        batch = manifest[i : i + batch_size]
        to_create = []
        for c in batch:
            chunk_uid = c.get("chunk_id") or ""
            if EmbeddingChunk.objects.filter(chunk_uid=chunk_uid).exists():
                skipped += 1
                continue
            to_create.append(c)

        if not to_create:
            skipped += len(batch)
            print(f"Loaded {min(i + batch_size, total)}/{total} chunks")
            continue

        texts = [get_page_text(args.pdf_dir, c["doc_hash"], c["page_num"]) for c in to_create]
        vectors = embed_texts(texts)

        for c, text, vec in zip(to_create, texts, vectors):
            doc_hash = c.get("doc_hash")
            doc = doc_by_hash.get(doc_hash)
            if not doc:
                continue
            EmbeddingChunk.objects.create(
                document=doc,
                chunk_uid=c.get("chunk_id", ""),
                chunk_index=int(c.get("page_num", 0)),
                text=text,
                embedding=vec,
                meta={
                    "doc_type": c.get("doc_type"),
                    "case_ref": c.get("case_ref"),
                    "law_name": c.get("law_name"),
                    "char_count": c.get("char_count"),
                    "page_num": c.get("page_num"),
                },
            )
            loaded += 1

        print(f"Loaded {min(i + batch_size, total)}/{total} chunks")

    print(f"\nSummary: {loaded} chunks loaded, {skipped} skipped (already existed), {total} total in manifest.")
    print(f"Documents: {len(doc_by_hash)}.")


if __name__ == "__main__":
    main()

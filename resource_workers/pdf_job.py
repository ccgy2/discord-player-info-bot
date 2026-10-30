"""PDF/embedding/Gemini work. Every invocation exits and returns its RAM to OS.

The embedding model and pdf_chunks schema intentionally match the original bot.
No re-indexing or migration of existing stored embeddings is required.
"""
from __future__ import annotations
import heapq
import json
import math
import os
from pathlib import Path
import re
from .protocol import JobError, main

MODEL_NAME = "all-MiniLM-L6-v2"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_PAGES = 1000


def split_text(text, size=500):
    return [text[i:i + size] for i in range(0, len(text), size)]


def extract_article(text):
    match = re.search(r"(제\s*\d+\s*조)", text)
    return match.group(1) if match else None


def get_database(request):
    import firebase_admin
    from firebase_admin import credentials, firestore
    try:
        app = firebase_admin.get_app()
    except ValueError:
        key = os.getenv("FIREBASE_KEY")
        credential_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        config = os.getenv("FIREBASE_CONFIG")
        if key:
            credential = credentials.Certificate(json.loads(key))
        elif credential_file and Path(credential_file).is_file():
            credential = credentials.Certificate(credential_file)
        elif config:
            data = json.loads(config)
            credential = credentials.Certificate(data) if data.get("type") == "service_account" else None
        else:
            credential = None
        # Explicitly keep the parent's project; never silently write another DB.
        options = {"projectId": request["project"]} if request.get("project") else None
        app = firebase_admin.initialize_app(credential, options=options)
    return firestore.client(app=app)


def get_model():
    import torch
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME, device="cpu")


def encode(model, chunks):
    return model.encode(chunks, batch_size=8, show_progress_bar=False)


def top_matches(query, documents, k=3):
    """Stream Firestore results and keep only k; ties retain original order."""
    import numpy as np
    query = np.asarray(query)
    qnorm = np.linalg.norm(query)
    if not qnorm or not np.isfinite(qnorm):
        return []
    heap = []
    for index, document in enumerate(documents):
        data = document.to_dict()
        try:
            vector = np.asarray(data["embedding"])
            if vector.shape != query.shape:
                continue
            norm = np.linalg.norm(vector)
            if not norm or not np.isfinite(norm):
                continue
            score = float(np.dot(query, vector) / (qnorm * norm))
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(score):
            continue
        entry = (score, -index, data)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif entry[:2] > heap[0][:2]:
            heapq.heapreplace(heap, entry)
    return [entry[2] for entry in sorted(heap, key=lambda item: item[:2], reverse=True)]


def extract_pdf(path):
    from pypdf import PdfReader
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_PDF_BYTES:
        raise JobError("too_large")
    with path.open("rb") as stream:
        reader = PdfReader(stream)
        if len(reader.pages) > MAX_PAGES:
            raise JobError("too_large")
        parts, count = [], 0
        for page in reader.pages:
            text = page.extract_text()
            if text:
                count += len(text) + 1
                if count > MAX_TEXT_CHARS:
                    raise JobError("too_large")
                parts.append(text + "\n")
    if not parts:
        raise JobError("empty_pdf")
    return "".join(parts)


def register_pdf(db, request, model_factory=get_model):
    text = extract_pdf(request["path"])
    chunks = split_text(text)
    model = model_factory()
    source = str(request["source"])[:255]
    collection = db.collection("pdf_chunks")
    # Batches prevent holding an entire document's embeddings simultaneously.
    for offset in range(0, len(chunks), 8):
        batch = chunks[offset:offset + 8]
        vectors = encode(model, batch)
        for chunk, vector in zip(batch, vectors):
            collection.add({"text": chunk, "source": source,
                            "article": extract_article(chunk), "embedding": vector.tolist()})
    return {"chunks": len(chunks), "source": source}


def answer(db, request, model_factory=get_model):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise JobError("missing_key")
    question = request["question"]
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise JobError("too_large")
    model = model_factory()
    query = encode(model, [question])[0]
    results = top_matches(query, db.collection("pdf_chunks").stream())
    if not results:
        return {"message": "관련 정보를 찾지 못했습니다."}
    context = ""
    for result in results:
        article = result.get("article")
        title = (f"[{result['source']} - {article}]" if article else f"[{result['source']}]")
        context += f"{title}\n{result['text']}\n\n"
    prompt = (
        f"[참고 문서]\n{context}\n\n[사용자 질문]\n{question}\n\n"
        "지시사항: 제공된 참고 문서의 내용만을 바탕으로 질문에 정확하고 친절하게 답변하세요. "
        "만약 문서 내용으로 답변을 알 수 없다면 억지로 지어내지 말고 관련 내용을 찾을 수 없다고 답변하세요."
    )
    from google import genai
    client = genai.Client(api_key=key)
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return {"message": (response.text or "AI가 답변을 생성하지 못했습니다.")[:2000]}
    finally:
        client.close()


def article(db, number):
    pattern = f"제{number}조"
    matches = []
    # Existing data sometimes contains spaces, exactly as in the old parser.
    for doc in db.collection("pdf_chunks").select(["text", "source", "article"]).stream():
        data = doc.to_dict()
        if data.get("article") == pattern:
            matches.append(data)
            if len(matches) == 3:
                break
    if not matches:
        return {"message": "조문을 찾지 못했습니다."}
    return {"message": "".join(f"[{d['source']} - {d['article']}]\n{d['text']}\n\n" for d in matches)[:2000]}


def list_pdf(db):
    # Names, not random chunk IDs; bound the result retained in memory.
    files, clipped = set(), False
    for doc in db.collection("pdf_chunks").select(["source"]).stream():
        source = str(doc.to_dict().get("source") or doc.id)[:255]
        files.add(source)
        if len(files) >= 100:
            clipped = True
            break
    if not files:
        return {"message": "등록된 PDF가 없습니다."}
    text = "📚 등록된 PDF 목록\n\n" + "\n".join(sorted(files))
    clipped = clipped or len(text) > 1980
    return {"message": text[:1980] + ("\n...(이하 생략)" if clipped else "")}


def handle(request):
    action = request.get("action")
    if action not in {"upload", "ask", "article", "list"}:
        raise JobError("invalid_file")
    db = get_database(request)
    try:
        if action == "upload":
            return register_pdf(db, request)
        if action == "ask":
            return answer(db, request)
        if action == "article":
            return article(db, str(request["number"])[:50])
        return list_pdf(db)
    finally:
        db.close()


if __name__ == "__main__":
    main(handle)

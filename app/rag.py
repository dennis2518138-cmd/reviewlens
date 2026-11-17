from __future__ import annotations

import chromadb

from app.config import CHROMA_PATH, get_embedder

COLLECTION = "reviews"
BATCH = 32
JUNK = ("", "此用户未填写评价", "。。。")


class BgeChromaEF:

    def __init__(self, model):
        self._m = model

    def __call__(self, input):
        return self._m.embed(list(input)).tolist()

    def embed_query(self, input):
        return self.__call__(input)

    def name(self):
        return "bge-small-zh-v1.5-onnx"


def get_collection(reset: bool = False):
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    return client.get_or_create_collection(
        COLLECTION,
        embedding_function=BgeChromaEF(get_embedder()),
        metadata={"hnsw:space": "cosine"},
    )


def index_reviews(rows: list[dict], reset: bool = False) -> dict:
    col = get_collection(reset=reset)
    docs, ids, metas, skipped = [], [], [], 0
    seen_texts: set[str] = set()
    for r in rows:
        text = (r.get("text") or "").strip()
        if text in JUNK or len(text) < 2 or text in seen_texts:
            skipped += 1
            continue
        seen_texts.add(text)
        docs.append(text)
        ids.append(r["review_id"])
        metas.append({"product_id": r["product_id"], "brand": r["brand"],
                      "category": r["category"], "sku": r["sku"],
                      "rating": int(r["rating"]), "date": r["date"]})
    for i in range(0, len(docs), BATCH):
        col.upsert(ids=ids[i:i + BATCH], documents=docs[i:i + BATCH], metadatas=metas[i:i + BATCH])
    return {"indexed": len(docs), "skipped": skipped, "count": col.count()}


def search(query: str, k: int = 3, where: dict | None = None) -> list[dict]:
    col = get_collection()
    r = col.query(query_texts=[query], n_results=k, where=where)
    out = []
    for i, doc in enumerate(r["documents"][0]):
        d = r["distances"][0][i]
        out.append({"id": r["ids"][0][i], "text": doc, "distance": round(d, 4),
                    "similarity": round(1 - d, 4), **r["metadatas"][0][i]})
    return out
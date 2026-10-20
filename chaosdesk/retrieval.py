import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set


DEFAULT_KB_DIR = (
    Path(__file__).resolve().parent.parent
    / "knowledge"
    / "articles"
)

STOP_WORDS = {
    "a", "an", "and", "are", "for", "i", "in", "is",
    "it", "my", "of", "on", "the", "to", "with",
}


@dataclass(frozen=True)
class RetrievedDocument:
    document_id: str
    text: str
    score: float


def tokenize(text: str) -> Set[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words - STOP_WORDS


def retrieve_documents(
    query: str,
    kb_dir: Path = DEFAULT_KB_DIR,
    limit: int = 3,
) -> List[RetrievedDocument]:
    """Rank local articles by query-word overlap."""

    if limit < 1:
        raise ValueError("limit must be at least 1")

    query_words = tokenize(query)

    if not query_words:
        return []

    return rank_documents(query_words, load_documents(kb_dir), limit)


def load_documents(kb_dir):

    if not kb_dir.is_dir():
        raise FileNotFoundError(
            f"Knowledge-base directory does not exist: {kb_dir}"
        )

    results = []
    seen_ids = set()

    for path in sorted(kb_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")

        id_match = re.search(
            r"^Document ID:\s*(KB-\d+)\s*$",
            text,
            flags=re.MULTILINE,
        )

        if id_match is None:
            raise ValueError(f"Missing or invalid document ID: {path.name}")

        document_id = id_match.group(1)

        if document_id in seen_ids:
            raise ValueError(f"Duplicate document ID: {document_id}")

        seen_ids.add(document_id)

        results.append(
            RetrievedDocument(
                document_id=document_id,
                text=text,
                score=0,
            )
        )

    return results


def rank_documents(query_words, documents, limit):
    results = []
    for doc in documents:
        shared_words = query_words & tokenize(doc.text)
        if shared_words:
            results.append(RetrievedDocument(doc.document_id, doc.text,
                                             len(shared_words) / len(query_words)))

    results.sort(
        key=lambda document: (-document.score, document.document_id)
    )

    return results[:limit]


class SnapshotRetriever:
    """Immutable startup snapshot; restart workers to roll out KB changes."""

    def __init__(self, kb_dir=DEFAULT_KB_DIR):
        self.documents = tuple(load_documents(kb_dir))
        if not self.documents:
            raise ValueError("Knowledge base must contain at least one article")
        self.index = tuple((doc, tokenize(doc.text)) for doc in self.documents)
        self.revision = hashlib.sha256("\n".join(
            doc.document_id + "\n" + doc.text for doc in self.documents
        ).encode()).hexdigest()

    def __call__(self, query, kb_dir=None, limit=3):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        words = tokenize(query)
        if not words:
            return []
        results = [RetrievedDocument(doc.document_id, doc.text, len(words & tokens) / len(words))
                   for doc, tokens in self.index if words & tokens]
        return sorted(results, key=lambda doc: (-doc.score, doc.document_id))[:limit]

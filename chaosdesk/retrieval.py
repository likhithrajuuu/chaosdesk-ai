import re
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

        shared_words = query_words & tokenize(text)

        if not shared_words:
            continue

        score = len(shared_words) / len(query_words)

        results.append(
            RetrievedDocument(
                document_id=document_id,
                text=text,
                score=score,
            )
        )

    results.sort(
        key=lambda document: (-document.score, document.document_id)
    )

    return results[:limit]
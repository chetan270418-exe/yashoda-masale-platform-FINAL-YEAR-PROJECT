"""RAG Retriever module (Decision D-010, Rule 63).

Chunks approved documents and performs lexical similarity search.
Returns matching chunks with full citation provenance (document, section, excerpt).
"""

import math
import re
from dataclasses import dataclass
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent / "knowledge_base"


@dataclass
class DocumentChunk:
    """A retrievable snippet from an approved document."""

    id: str
    document: str
    section: str
    text: str
    tokens: list[str]


STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "what",
        "which",
        "are",
        "was",
        "were",
        "been",
        "have",
        "has",
        "had",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "you",
        "your",
        "they",
        "them",
        "their",
        "our",
        "all",
        "any",
        "some",
        "out",
        "about",
        "into",
        "over",
        "after",
        "how",
        "why",
        "when",
        "where",
        "ignore",
        "instructions",
        "previous",
        "print",
        "secret",
        "internal",
        "guidelines",
        "facts",
    }
)


def _tokenize(text: str) -> list[str]:
    """Tokenize and normalize text into clean lower-case words excluding common stop words."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in cleaned.split() if len(w) > 2 and w not in STOP_WORDS]


class KnowledgeRetriever:
    """In-memory indexed retriever for approved company knowledge base."""

    def __init__(self, kb_dir: Path | str | None = None) -> None:
        self.kb_dir = Path(kb_dir) if kb_dir else KB_DIR
        self.chunks: list[DocumentChunk] = []
        self._doc_frequencies: dict[str, int] = {}
        self.load_documents()

    def load_documents(self) -> None:
        """Scan knowledge base directory and chunk markdown documents by headers."""
        self.chunks.clear()
        self._doc_frequencies.clear()

        if not self.kb_dir.exists():
            return

        for path in self.kb_dir.glob("*.md"):
            doc_name = path.name
            content = path.read_text(encoding="utf-8")
            sections = re.split(r"\n(?=##?\s)", content)

            chunk_idx = 0
            for sec in sections:
                lines = sec.strip().split("\n")
                if not lines or not lines[0].strip():
                    continue

                header_line = lines[0].strip()
                section_title = re.sub(r"^#+\s*", "", header_line)
                body = "\n".join(lines[1:]).strip() if len(lines) > 1 else header_line

                if not body:
                    body = header_line

                tokens = _tokenize(section_title + " " + body)
                if not tokens:
                    continue

                chunk_id = f"{doc_name}:{chunk_idx}"
                chunk = DocumentChunk(
                    id=chunk_id,
                    document=doc_name,
                    section=section_title,
                    text=f"### {section_title}\n{body}",
                    tokens=tokens,
                )
                self.chunks.append(chunk)
                chunk_idx += 1

                for t in set(tokens):
                    self._doc_frequencies[t] = self._doc_frequencies.get(t, 0) + 1

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        min_score: float = 0.05,
    ) -> list[tuple[DocumentChunk, float]]:
        """Retrieve top-k relevant chunks scored via BM25/TF-IDF similarity."""
        query_tokens = _tokenize(query)
        if not query_tokens or not self.chunks:
            return []

        num_chunks = len(self.chunks)
        scores: list[tuple[DocumentChunk, float]] = []

        for chunk in self.chunks:
            chunk_token_set = set(chunk.tokens)
            score = 0.0

            for q in query_tokens:
                if q in chunk_token_set:
                    df = self._doc_frequencies.get(q, 1)
                    idf = math.log(1.0 + (num_chunks - df + 0.5) / (df + 0.5))
                    tf = chunk.tokens.count(q) / len(chunk.tokens)
                    score += idf * tf

            if score >= min_score:
                scores.append((chunk, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# Singleton instance
_retriever_instance: KnowledgeRetriever | None = None


def get_retriever() -> KnowledgeRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = KnowledgeRetriever()
    return _retriever_instance

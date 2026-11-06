"""Picks the most similar past cover letters to a job description.

CLAUDE.md calls for "embedding similarity", but no embeddings-provider
credential is in this project's `.env.example` (see DECISIONS.md) — Voyage
AI/OpenAI embeddings would need a new API key we don't have, and CLAUDE.md
says to stop and ask before requiring a credential that isn't provided
rather than build around it. This is a local, dependency-free TF-IDF cosine
similarity instead: no API call, no extra cost, good enough to rank a
handful of past letters. `most_similar()` is the only entry point other
code should depend on, so swapping in real embeddings later is a one-function
change.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _tfidf_vectors(documents: dict[str, str]) -> dict[str, Counter[str]]:
    term_frequencies = {name: Counter(_tokenize(text)) for name, text in documents.items()}

    doc_count = len(term_frequencies)
    doc_frequency: Counter[str] = Counter()
    for tf in term_frequencies.values():
        doc_frequency.update(tf.keys())

    idf = {term: math.log((1 + doc_count) / (1 + df)) + 1 for term, df in doc_frequency.items()}

    vectors: dict[str, Counter[str]] = {}
    for name, tf in term_frequencies.items():
        vectors[name] = Counter({term: count * idf.get(term, 0.0) for term, count in tf.items()})
    return vectors


def _cosine_similarity(a: Counter[str], b: Counter[str]) -> float:
    shared_terms = set(a) & set(b)
    dot_product = sum(a[term] * b[term] for term in shared_terms)
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def most_similar(query_text: str, candidates: dict[str, str], top_n: int = 3) -> list[str]:
    """Returns up to `top_n` candidate names, most similar to `query_text` first."""
    if not candidates:
        return []

    documents = {"__query__": query_text, **candidates}
    vectors = _tfidf_vectors(documents)
    query_vector = vectors["__query__"]

    scored = [
        (name, _cosine_similarity(query_vector, vectors[name])) for name in candidates
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [name for name, _score in scored[:top_n]]

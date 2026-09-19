"""中文字符二元组 + 英文词 BM25。无 embedding，无外部下载。"""
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(span[i:i + 2] for i in range(len(span) - 1))
    return tokens


class BM25:
    def __init__(self, texts: list[str]):
        self.counts = [Counter(tokenize(text)) for text in texts]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = sum(self.lengths) / max(1, len(texts)) or 1
        self.df = Counter(token for c in self.counts for token in c)

    def search(self, query: str, limit: int = 4, min_overlap: int = 1) -> list[tuple[int, float]]:
        terms = set(tokenize(query))
        ranked = []
        for i, counts in enumerate(self.counts):
            matched = terms & counts.keys()
            if len(matched) < min_overlap:
                continue
            score = 0.0
            for term in matched:
                frequency = counts[term]
                idf = math.log(1 + (len(self.counts) - self.df[term] + 0.5) / (self.df[term] + 0.5))
                score += idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * self.lengths[i] / self.average))
            ranked.append((i, score))
        return sorted(ranked, key=lambda row: (-row[1], row[0]))[:limit]


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str
    text: str
    source: str
    version: str
    kind: str


class KnowledgeIndex:
    def __init__(self, directory: Path):
        self.chunks: list[Chunk] = []
        # 仅显式知识目录中的 JSON，绝不自动扫描配置、日志或整个工作区。
        seen = set()
        for path in sorted(directory.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            for key in ("id", "title", "source", "version", "kind", "text"):
                if not isinstance(doc.get(key), str) or not doc[key].strip():
                    raise ValueError(f"知识文档 {path.name} 缺少有效的 {key}")
            if doc["id"] in seen:
                raise ValueError("知识文档 ID 重复")
            seen.add(doc["id"])
            for start in range(0, len(doc["text"]), 360):
                text = doc["text"][start:start + 420]
                digest = hashlib.sha256((doc["version"] + text).encode()).hexdigest()[:10]
                self.chunks.append(Chunk(f"{doc['id']}-{start}-{digest}", doc["title"], text,
                                         doc["source"], doc["version"], doc["kind"]))
                if start + 420 >= len(doc["text"]):
                    break
        self.index = BM25([c.title + " " + c.text for c in self.chunks])

    def retrieve(self, query: str, limit: int = 3) -> list[dict]:
        hits = self.index.search(query, limit, min_overlap=2)
        return [{**self.chunks[i].__dict__, "score": round(score, 4)} for i, score in hits]

"""中文字符二元组 + 英文词 BM25。无 embedding，无外部下载。"""
import math
import re
from collections import Counter


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



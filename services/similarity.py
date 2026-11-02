import re
from difflib import SequenceMatcher

STOP = {
    "и","в","на","с","по","для","из","к","о","у","от","до","за","а","или","но",
    "the","a","an","of","to","in","for","and","or","is","are",
}


def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    sm = SequenceMatcher(None, na, nb).ratio()
    wa = set(na.split()) - STOP
    wb = set(nb.split()) - STOP
    jacc = (len(wa & wb) / len(wa | wb)) if (wa or wb) else 0.0
    return round(0.45 * sm + 0.55 * jacc, 3)


def find_similar_topics(query: str, topics: list[tuple[int, str]], top_n: int = 3) -> list[tuple[int, str, float]]:
    scored = [(tid, title, similarity(query, title)) for tid, title in topics]
    scored.sort(key=lambda x: -x[2])
    return scored[:top_n]

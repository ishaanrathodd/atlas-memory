from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files

from memory.models import EmotionProfile


DEFAULT_EMOTIONS = (
    "joy",
    "sadness",
    "anger",
    "fear",
    "surprise",
    "anticipation",
    "trust",
    "disgust",
)


class EmotionAnalyzer:
    """Deterministic emotional analysis using a bundled NRC-style lexicon."""

    def __init__(self, lexicon_path: str | None = None) -> None:
        self.lexicon = self._load_lexicon(lexicon_path)

    def analyze(self, text: str) -> EmotionProfile:
        tokens = self._tokenize(text)
        counts = {emotion: 0.0 for emotion in DEFAULT_EMOTIONS}
        total_hits = 0
        for token in tokens:
            emotions = self.lexicon.get(token)
            if not emotions:
                continue
            total_hits += 1
            for emotion in emotions:
                if emotion in counts:
                    counts[emotion] += 1.0

        if total_hits == 0:
            return EmotionProfile(scores=counts, dominant_emotion=None, intensity=0.0)

        normalized = {emotion: score / total_hits for emotion, score in counts.items()}
        dominant_emotion = max(normalized, key=normalized.get)
        intensity = max(normalized.values())
        return EmotionProfile(scores=normalized, dominant_emotion=dominant_emotion, intensity=float(intensity))

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z']+", text.lower())

    @staticmethod
    @lru_cache(maxsize=1)
    def _default_lexicon() -> dict[str, list[str]]:
        resource = files("memory.data").joinpath("nrc_lexicon.json")
        with resource.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_lexicon(self, lexicon_path: str | None) -> dict[str, list[str]]:
        if lexicon_path:
            with open(lexicon_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        return self._default_lexicon()

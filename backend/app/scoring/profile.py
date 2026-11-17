"""My skills (data/private/profile.yaml) and the skill vocabulary (config/skills.yaml)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LEVEL_WEIGHT = {"strong": 1.0, "moderate": 0.6}


@dataclass
class Experience:
    name: str
    skills: list[str]


@dataclass
class Profile:
    skills: dict[str, dict[str, str]]  # canonical skill -> {level, evidence}
    experiences: list[Experience] = field(default_factory=list)

    def weight(self, skill: str) -> float:
        info = self.skills.get(skill)
        return LEVEL_WEIGHT.get(info["level"], 0.0) if info else 0.0


_LITERAL_PREFIX = re.compile(r"[a-z0-9&/ .-]+")


def _anchor(pattern: str) -> str | None:
    """A literal substring every match must contain, used as a cheap pre-check (None = always run the regex)."""
    m = _LITERAL_PREFIX.match(pattern)
    if not m:
        return None
    literal = m.group(0)
    # A trailing char followed by '?' or '*' is optional, so it can't be part of the anchor.
    if pattern[m.end():m.end() + 1] in ("?", "*"):
        literal = literal[:-1]
    return literal if len(literal) >= 3 else None


class SkillVocabulary:
    def __init__(self, patterns: dict[str, list[str]]):
        self.patterns = {
            name: re.compile(r"(?<![\w])(?:" + "|".join(p) + r")(?![\w])") for name, p in patterns.items()
        }
        # If any of a skill's patterns has no anchor, the skill's regex always runs.
        self.anchors: dict[str, list[str] | None] = {}
        for name, plist in patterns.items():
            anchors = [_anchor(p.lower()) for p in plist]
            self.anchors[name] = None if None in anchors else anchors

    def find(self, text: str) -> list[str]:
        lowered = text.lower()
        found = []
        for name, pattern in self.patterns.items():
            anchors = self.anchors[name]
            if anchors is not None and not any(a in lowered for a in anchors):
                continue
            if pattern.search(lowered):
                found.append(name)
        return found


def load_vocabulary(path: Path) -> SkillVocabulary:
    data = yaml.safe_load(path.read_text()) or {}
    skills = data.get("skills") or {}
    if not skills:
        raise ValueError(f"{path.name}: no skills defined")
    return SkillVocabulary({str(k): [str(p) for p in v] for k, v in skills.items()})


def load_profile(path: Path, vocabulary: SkillVocabulary) -> Profile:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; create it from your resume (see README)")
    data = yaml.safe_load(path.read_text()) or {}
    skills = data.get("skills") or {}
    for name, info in skills.items():
        if name not in vocabulary.patterns:
            raise ValueError(f"{path.name}: skill {name!r} is not in config/skills.yaml")
        if (info or {}).get("level") not in LEVEL_WEIGHT:
            raise ValueError(f"{path.name}: skill {name!r} needs level strong or moderate")
    experiences = [Experience(str(e["name"]), list(e.get("skills") or [])) for e in data.get("experiences") or []]
    return Profile(skills, experiences)

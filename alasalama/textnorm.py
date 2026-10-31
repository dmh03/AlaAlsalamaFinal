import re


def norm(text: str) -> str:
    """Lower-case, strip Arabic diacritics/tatweel, fold common Arabic letter variants, drop punctuation and emoji."""
    t = re.sub(r"[\u064B-\u0652\u0640]", "", text.lower())
    t = t.translate(str.maketrans("أإآىة", "ااايه"))
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())

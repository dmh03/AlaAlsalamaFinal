from functools import lru_cache

import yaml

from .db import ROOT


@lru_cache
def load_template(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / "templates" / f"{name}.yaml").read_text(encoding="utf-8"))


def template_for_code(code: str) -> str:
    """Deterministic mapping procedure code -> template."""
    for p in (ROOT / "config" / "templates").glob("*.yaml"):
        t = yaml.safe_load(p.read_text(encoding="utf-8"))
        if code in t["procedure_codes"]:
            return t["name"]
    raise KeyError(f"no template for procedure code {code}")


@lru_cache
def rules() -> dict:
    return yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())

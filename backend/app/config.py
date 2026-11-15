"""Runtime configuration: the rules policy file and environment settings.

The rules file is deliberately loaded as data rather than mirrored into Python
constants, so the thresholds the engine applies and the thresholds the UI
displays are the same object.
"""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = Path(os.environ.get("RULES_PATH", ROOT / "config" / "rules.yaml"))
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", ROOT / "sample_invoices"))


#: The file types the sample gallery may hold, longest-standing first. A
#: sample is addressed by its stem alone ("00_happy_path_bharat_steel"), so the
#: extension has to be resolved rather than assumed - the gallery now contains
#: a JPG as well as PDFs.
SAMPLE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".webp")


def sample_path(key: str) -> Path | None:
    """The file behind a sample key, or None if there is no such sample.

    Resolves inside SAMPLES_DIR and refuses anything that escapes it, because
    the key arrives from a public endpoint.
    """
    for suffix in SAMPLE_SUFFIXES:
        candidate = (SAMPLES_DIR / f"{key}{suffix}").resolve()
        if (candidate.is_file()
                and SAMPLES_DIR.resolve() in candidate.parents):
            return candidate
    return None


class Rules:
    """Typed, dotted access to rules.yaml with no silent defaults.

    A missing key raises rather than falling back, because a tolerance that
    quietly defaults to zero would change decisions without anyone noticing.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    def get(self, path: str) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"rules.yaml is missing '{path}'")
            node = node[part]
        return node

    def decimal(self, path: str) -> Decimal:
        return Decimal(str(self.get(path)))

    def number(self, path: str) -> float:
        return float(self.get(path))

    def integer(self, path: str) -> int:
        return int(self.get(path))

    def boolean(self, path: str) -> bool:
        return bool(self.get(path))

    def strings(self, path: str) -> list[str]:
        return [str(v) for v in self.get(path)]

    # -- the handful used often enough to deserve names ---------------------
    @property
    def total_variance_pct(self) -> Decimal:
        return self.decimal("tolerance.total_variance_pct")

    @property
    def total_variance_abs(self) -> Decimal:
        return self.decimal("tolerance.total_variance_abs")

    @property
    def rounding_abs(self) -> Decimal:
        return self.decimal("tolerance.arithmetic_rounding_abs")

    def tolerance_band(self, expected: Decimal) -> tuple[Decimal, str]:
        """The permitted absolute variance on an expected amount, and its label.

        Policy is "2% or Rs 500, whichever is SMALLER": the percentage alone
        becomes too generous on large invoices, so the absolute cap bounds the
        exposure on any single document.
        """
        pct_band = (abs(expected) * self.total_variance_pct / 100).quantize(Decimal("0.01"))
        abs_band = self.total_variance_abs
        if pct_band <= abs_band:
            return pct_band, f"{self.total_variance_pct.normalize():f}% ({pct_band})"
        return abs_band, f"{abs_band} cap"


@lru_cache(maxsize=1)
def get_rules() -> Rules:
    with RULES_PATH.open(encoding="utf-8") as fh:
        return Rules(yaml.safe_load(fh))


def reload_rules() -> Rules:
    get_rules.cache_clear()
    return get_rules()


def _env(name: str, default: str = "") -> str:
    """Read an environment variable, tolerating a value wrapped in quotes.

    Pasting a key into a .env file with the quotes still around it is an easy
    mistake to make and produces a baffling 401 rather than a clear error.
    """
    return os.environ.get(name, default).strip().strip('"').strip("'")


class Settings:
    """Environment-driven settings. Secrets are never defaulted to a value."""

    def __init__(self) -> None:
        self.gemini_api_key = _env("GEMINI_API_KEY")
        self.gemini_model = _env("GEMINI_MODEL", "gemini-3.5-flash")
        self.gemini_fallback_model = os.environ.get(
            "GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
        self.gemini_timeout = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "60"))
        self.gemini_max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
        self.database_url = os.environ.get(
            "DATABASE_URL", f"sqlite:///{ROOT / 'invoice_pipeline.db'}")
        self.app_env = _env("APP_ENV", "local")
        self.auto_seed = os.environ.get("AUTO_SEED_ON_BOOT", "true").lower() == "true"
        self.log_level = _env("LOG_LEVEL", "INFO")

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

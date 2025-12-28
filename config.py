from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

@dataclass(frozen=True)
class AppConfig:
    CHECKS_PATH: Path = BASE_DIR / "data" / "seo_checks_mvp01.json"
    SCORING_PATH: Path = BASE_DIR / "data" / "scoring_model_mvp01.json"
    DEFAULT_USER_AGENT: str = "SeoCheckerBot/0.1 (+local dev)"
    REQUEST_TIMEOUT_S: int = 15
    MAX_FETCH_BYTES: int = 2_000_000  # 2MB HTML cap for MVP
    DEFAULT_MAX_PAGES_FREE: int = 10
    DEFAULT_MAX_DEPTH: int = 2

CONFIG = AppConfig()

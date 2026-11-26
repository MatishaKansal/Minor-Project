from __future__ import annotations

import os
from pathlib import Path

PHASE4_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PHASE4_ROOT.parent

DATA_DIR = PHASE4_ROOT / "data"
BENCHMARK_PATH = DATA_DIR / "benchmark_120.json"
TRACES_DIR = PROJECT_ROOT / "traces" / "phase4"
RESULTS_DIR = PROJECT_ROOT / "results" / "phase4"
ROUTERS_DIR = PHASE4_ROOT / "routers"

# Model Ladder configuration.
# Defaults to gemma4:cloud for fast answers during testing
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:cloud")
FAST_MODE = os.environ.get("FAST_MODE", "true").lower() in ("true", "1", "yes")

PHASE4_MODEL_CONFIG = {
    "layer_1": {
        "key": "layer_1",
        "model": OLLAMA_MODEL,
        "name": "gemma4-cloud",
        "cost_per_1k_tokens": 0.0005,
    },
    "layer_2": {
        "key": "layer_2",
        "model": OLLAMA_MODEL,
        "name": "gemma4-cloud",
        "cost_per_1k_tokens": 0.0010,
    },
    "layer_3": {
        "key": "layer_3",
        "model": OLLAMA_MODEL,
        "name": "gemma4-cloud",
        "cost_per_1k_tokens": 0.0020,
    },
    "layer_4": {
        "key": "layer_4",
        "model": OLLAMA_MODEL,
        "name": "gemma4-cloud",
        "cost_per_1k_tokens": 0.0040,
    },
}

DEFAULT_JUDGE_MODEL = OLLAMA_MODEL
CHEAP_JUDGE_MODEL = "qwen3.5:0.8b"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Cascade parameters
MAX_FALSE_STOP_RATE = 0.05
DEFAULT_TEMPERATURE = 0.1
DEFAULT_SEED = 42
DEFAULT_MAX_TOKENS = int(os.environ.get("MAX_PREDICT_TOKENS", "384"))

# Database configuration (zero-secret by default)
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
SQLITE_DB_PATH = DATA_DIR / "audit_fixture.db"

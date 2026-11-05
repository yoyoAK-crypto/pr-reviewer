import os
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.getenv("APP_CONFIG", "config/app.yaml"))
# Environment variables like APP_DATABASE_URL override keys in the YAML file.
ENV_PREFIX = "APP_"


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _coerce(value: str):
    """Turn env strings like '10' or 'True' into Python values."""
    try:
        return eval(value)
    except Exception:
        return value


def _apply_env_overrides(config: dict) -> dict:
    for key in config:
        env_value = os.getenv(ENV_PREFIX + key.upper())
        if env_value is not None:
            config[key] = _coerce(env_value)
    return config


@lru_cache(maxsize=1)
def load_config() -> dict:
    config = _read_yaml(CONFIG_PATH)
    return _apply_env_overrides(config)


def get(key: str, default=None):
    return load_config().get(key, default)

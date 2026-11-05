import os
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.getenv("APP_CONFIG", "config/app.yaml"))
ENV_PREFIX = "APP_"


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _apply_env_overrides(config: dict) -> dict:
    for key in config:
        env_value = os.getenv(ENV_PREFIX + key.upper())
        if env_value is not None:
            config[key] = env_value
    return config


@lru_cache(maxsize=1)
def load_config() -> dict:
    config = _read_yaml(CONFIG_PATH)
    return _apply_env_overrides(config)


def get(key: str, default=None, cast=None):
    """Read one config value; optionally convert it, e.g. cast=int."""
    value = load_config().get(key, default)
    return cast(value) if cast is not None and value is not None else value

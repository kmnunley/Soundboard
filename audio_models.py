from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass
class CompressorSettings:
    enabled: bool
    input_gain_db: float
    threshold_db: float
    ratio: float
    attack_ms: float
    release_ms: float
    makeup_gain_db: float
    output_ceiling_db: float
    cache_max_items: int
    revision: int = 0


DEFAULT_COMPRESSOR_SETTINGS = CompressorSettings(
    enabled=True,
    input_gain_db=0.0,
    threshold_db=-18.0,
    ratio=4.0,
    attack_ms=10.0,
    release_ms=120.0,
    makeup_gain_db=0.0,
    output_ceiling_db=-1.0,
    cache_max_items=32,
    revision=0,
)


def _num(value, fallback):
    if isinstance(value, (int, float)):
        return value
    return fallback


def compressor_settings_from_dict(data: dict) -> CompressorSettings:
    settings = replace(DEFAULT_COMPRESSOR_SETTINGS)
    settings.enabled = bool(data.get("compressor_enabled", settings.enabled))
    settings.input_gain_db = float(_num(data.get("compressor_input_gain_db"), settings.input_gain_db))
    settings.threshold_db = float(_num(data.get("compressor_threshold_db"), settings.threshold_db))
    settings.ratio = max(1.0, float(_num(data.get("compressor_ratio"), settings.ratio)))
    settings.attack_ms = max(1.0, float(_num(data.get("compressor_attack_ms"), settings.attack_ms)))
    settings.release_ms = max(1.0, float(_num(data.get("compressor_release_ms"), settings.release_ms)))
    settings.makeup_gain_db = float(_num(data.get("compressor_makeup_gain_db"), settings.makeup_gain_db))
    settings.output_ceiling_db = float(_num(data.get("compressor_output_ceiling_db"), settings.output_ceiling_db))
    settings.cache_max_items = max(1, int(_num(data.get("compressor_cache_max_items"), settings.cache_max_items)))
    settings.revision = max(0, int(_num(data.get("compressor_revision"), settings.revision)))
    return settings


def compressor_settings_to_dict(settings: CompressorSettings) -> dict:
    return {
        "compressor_enabled": bool(settings.enabled),
        "compressor_input_gain_db": float(settings.input_gain_db),
        "compressor_threshold_db": float(settings.threshold_db),
        "compressor_ratio": float(settings.ratio),
        "compressor_attack_ms": float(settings.attack_ms),
        "compressor_release_ms": float(settings.release_ms),
        "compressor_makeup_gain_db": float(settings.makeup_gain_db),
        "compressor_output_ceiling_db": float(settings.output_ceiling_db),
        "compressor_cache_max_items": int(settings.cache_max_items),
        "compressor_revision": int(settings.revision),
    }

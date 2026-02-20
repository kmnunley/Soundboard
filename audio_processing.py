from __future__ import annotations

import math

import numpy as np
import pygame

from audio_models import CompressorSettings


class CompressorEngine:
    def __init__(self):
        self._eps = 1e-9

    def process(self, sound: pygame.mixer.Sound, settings: CompressorSettings) -> pygame.mixer.Sound:
        samples, original_dtype = self._sound_to_float(sound)
        if samples.size == 0:
            return sound

        processed = self._apply_chain(samples, settings)
        return self._float_to_sound(processed, original_dtype)

    def _apply_chain(self, samples: np.ndarray, settings: CompressorSettings) -> np.ndarray:
        sample_rate = pygame.mixer.get_init()[0]

        data = samples * self._db_to_linear(settings.input_gain_db)
        level = np.max(np.abs(data), axis=1) if data.ndim == 2 else np.abs(data)
        gain = self._compressor_gain(level, sample_rate, settings)

        if data.ndim == 2:
            data = data * gain[:, None]
        else:
            data = data * gain

        data = data * self._db_to_linear(settings.makeup_gain_db)
        ceiling = max(self._eps, self._db_to_linear(settings.output_ceiling_db))
        data = np.clip(data, -ceiling, ceiling)
        return data.astype(np.float32, copy=False)

    def _compressor_gain(self, level: np.ndarray, sample_rate: int, settings: CompressorSettings) -> np.ndarray:
        attack_s = max(0.001, settings.attack_ms / 1000.0)
        release_s = max(0.001, settings.release_ms / 1000.0)
        attack_coeff = math.exp(-1.0 / (attack_s * sample_rate))
        release_coeff = math.exp(-1.0 / (release_s * sample_rate))

        threshold = settings.threshold_db
        ratio = max(1.0, settings.ratio)
        env = 0.0

        gain = np.ones(level.shape[0], dtype=np.float32)
        for i, peak in enumerate(level):
            coeff = attack_coeff if peak > env else release_coeff
            env = (coeff * env) + ((1.0 - coeff) * float(peak))
            env_db = 20.0 * math.log10(max(env, self._eps))
            if env_db <= threshold:
                continue

            over_db = env_db - threshold
            compressed_db = threshold + (over_db / ratio)
            reduction_db = compressed_db - env_db
            gain[i] = self._db_to_linear(reduction_db)

        return gain

    def _sound_to_float(self, sound: pygame.mixer.Sound) -> tuple[np.ndarray, np.dtype]:
        arr = pygame.sndarray.array(sound)
        original_dtype = arr.dtype

        if arr.dtype.kind == "u":
            max_int = float(np.iinfo(arr.dtype).max)
            data = (arr.astype(np.float32) / max_int) * 2.0 - 1.0
        elif arr.dtype.kind in ("i", "b"):
            max_abs = float(max(abs(np.iinfo(arr.dtype).min), np.iinfo(arr.dtype).max))
            data = arr.astype(np.float32) / max_abs
        else:
            data = arr.astype(np.float32)

        return data, original_dtype

    def _float_to_sound(self, data: np.ndarray, original_dtype: np.dtype) -> pygame.mixer.Sound:
        if original_dtype.kind == "u":
            max_int = float(np.iinfo(original_dtype).max)
            scaled = ((np.clip(data, -1.0, 1.0) + 1.0) * 0.5 * max_int).astype(original_dtype)
        elif original_dtype.kind in ("i", "b"):
            iinfo = np.iinfo(original_dtype)
            max_int = float(iinfo.max)
            scaled = (np.clip(data, -1.0, 1.0) * max_int).astype(original_dtype)
        else:
            scaled = np.clip(data, -1.0, 1.0).astype(np.float32)

        return pygame.sndarray.make_sound(scaled)

    @staticmethod
    def _db_to_linear(db: float) -> float:
        return 10.0 ** (float(db) / 20.0)

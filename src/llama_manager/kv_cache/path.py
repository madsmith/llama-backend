from __future__ import annotations

from pathlib import Path

from llama_manager.config import AppConfig


def resolve_slot_save_path(cfg: AppConfig, model_index: int) -> Path | None:
    """Return the slot save directory for a model, or None if kv_cache is off."""
    if model_index >= len(cfg.models):
        return None  # federated remote model — no local config
    model = cfg.models[model_index]
    adv = model.advanced

    if not adv.kv_cache:
        return None

    if adv.slot_save_path:
        return Path(adv.slot_save_path).expanduser().resolve()

    base = cfg.web_ui.slot_save_path or "./slot_saves"
    model_id = model.effective_id or f"model-{model_index}"

    return Path(base).expanduser().resolve() / model_id

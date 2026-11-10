"""Read GGUF header metadata without touching the tensor data.

Only the key/value header is parsed, so even multi-GB files are read in a
fraction of a second. Large numeric arrays are skipped; string arrays (the
tokenizer vocabulary) are walked to count them but not kept.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any, BinaryIO

GGUF_MAGIC = b"GGUF"

_SCALAR = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
    6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
_STRING = 8
_ARRAY = 9

# Arrays longer than this are summarised as their length instead of kept.
_MAX_KEPT_ARRAY = 1024

_SHARD_RE = re.compile(r"^(?P<stem>.*)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$")

_SAMPLING_KEYS = {
    "temp": "temperature",
    "top_k": "top_k",
    "top_p": "top_p",
    "min_p": "min_p",
    "penalty_repeat": "repeat_penalty",
}


class GGUFError(ValueError):
    pass


class ArrayLen(int):
    """Stand-in for an array that was too long to keep: just its length."""


def _read(f: BinaryIO, fmt: str) -> Any:
    size = struct.calcsize(fmt)
    data = f.read(size)
    if len(data) != size:
        raise GGUFError("unexpected end of file in header")
    return struct.unpack(fmt, data)[0]


def _read_str(f: BinaryIO) -> str:
    n = _read(f, "<Q")
    data = f.read(n)
    if len(data) != n:
        raise GGUFError("unexpected end of file in header")
    return data.decode("utf-8", "replace")


def _read_value(f: BinaryIO, vtype: int) -> Any:
    if vtype in _SCALAR:
        return _read(f, _SCALAR[vtype])
    if vtype == _STRING:
        return _read_str(f)
    if vtype == _ARRAY:
        itype, n = _read(f, "<I"), _read(f, "<Q")
        if n > _MAX_KEPT_ARRAY:
            if itype in _SCALAR:
                f.seek(struct.calcsize(_SCALAR[itype]) * n, 1)
            elif itype == _STRING:
                for _ in range(n):
                    f.seek(_read(f, "<Q"), 1)
            else:
                for _ in range(n):
                    _read_value(f, itype)
            return ArrayLen(n)
        return [_read_value(f, itype) for _ in range(n)]
    raise GGUFError(f"unknown GGUF value type {vtype}")


def read_metadata(path: str | Path) -> dict[str, Any]:
    """Return the header key/value pairs of a GGUF file."""
    path = Path(path).expanduser()
    with path.open("rb") as f:
        if f.read(4) != GGUF_MAGIC:
            raise GGUFError("not a GGUF file")
        version = _read(f, "<I")
        if version < 2:
            raise GGUFError(f"unsupported GGUF version {version}")
        _read(f, "<Q")  # tensor count
        n_kv = _read(f, "<Q")
        meta: dict[str, Any] = {}
        for _ in range(n_kv):
            key = _read_str(f)
            meta[key] = _read_value(f, _read(f, "<I"))
        return meta


def metadata_file(path: str | Path) -> Path:
    """Split models keep their metadata in shard 1; map any shard to it."""
    path = Path(path).expanduser()
    m = _SHARD_RE.match(path.name)
    if m and m["idx"] != "00001":
        first = path.with_name(f"{m['stem']}-00001-of-{m['total']}.gguf")
        if first.is_file():
            return first
    return path


def _num(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _per_layer(v: Any, n_layers: int) -> list[int] | None:
    """A per-layer value given either as one number or as a per-layer array."""
    if isinstance(v, list) and len(v) == n_layers and all(_num(x) is not None for x in v):
        return [int(x) for x in v]
    n = _num(v)
    return [n] * n_layers if n is not None else None


def _kv_cache(meta: dict[str, Any], arch: str, n_layers: int | None) -> dict[str, Any] | None:
    """f16 KV bytes per token, split into full-attention and sliding-window layers.

    Returns None for architectures whose cache this simple model can't
    describe (recurrent/hybrid layers, MLA, missing keys).
    """
    if not n_layers:
        return None
    a = f"{arch}."
    if any(k.startswith(a + "ssm.") or k.startswith(a + "attention.kv_lora_rank") for k in meta):
        return None
    heads_kv = _per_layer(meta.get(a + "attention.head_count_kv", meta.get(a + "attention.head_count")), n_layers)
    if heads_kv is None:
        return None
    k_len = _num(meta.get(a + "attention.key_length"))
    v_len = _num(meta.get(a + "attention.value_length"))
    if k_len is None or v_len is None:
        embd, heads = _num(meta.get(a + "embedding_length")), _num(meta.get(a + "attention.head_count"))
        if not embd or not heads:
            return None
        k_len = k_len or embd // heads
        v_len = v_len or embd // heads

    # Some models (gemma4) use smaller heads on their sliding-window layers.
    k_swa = _num(meta.get(a + "attention.key_length_swa")) or k_len
    v_swa = _num(meta.get(a + "attention.value_length_swa")) or v_len

    window = _num(meta.get(a + "attention.sliding_window")) or 0
    pattern = meta.get(a + "attention.sliding_window_pattern")
    if window and isinstance(pattern, list) and len(pattern) == n_layers:
        swa_layers = [bool(x) for x in pattern]
    else:
        # No usable pattern: treat every layer as full attention (an upper bound).
        swa_layers = [False] * n_layers
        window = 0 if not isinstance(pattern, list) else window

    full = swa = 0
    for heads, is_swa in zip(heads_kv, swa_layers):
        if is_swa:
            swa += heads * (k_swa + v_swa) * 2  # f16
        else:
            full += heads * (k_len + v_len) * 2
    return {"full_bytes_per_token": full, "swa_bytes_per_token": swa, "sliding_window": window or None}


def summarize(path: str | Path) -> dict[str, Any]:
    """The parts of a GGUF header that configure llama-server."""
    src = metadata_file(path)
    meta = read_metadata(src)
    arch = str(meta.get("general.architecture") or "")
    a = f"{arch}."
    n_layers = _num(meta.get(a + "block_count"))

    tokens = meta.get("tokenizer.ggml.tokens")
    vocab = len(tokens) if isinstance(tokens, list) else (int(tokens) if isinstance(tokens, ArrayLen) else None)

    sampling = {}
    for key, field in _SAMPLING_KEYS.items():
        v = meta.get(f"general.sampling.{key}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            sampling[field] = round(v, 6) if isinstance(v, float) else v

    info: dict[str, Any] = {
        "path": str(Path(path).expanduser()),
        "type": meta.get("general.type") or ("mmproj" if arch == "clip" else "model"),
        "architecture": arch or None,
        "name": meta.get("general.name"),
        "context_length": _num(meta.get(a + "context_length")),
        "block_count": n_layers,
        "embedding_length": _num(meta.get(a + "embedding_length")),
        "has_chat_template": isinstance(meta.get("tokenizer.chat_template"), str),
        "tokenizer": {
            "model": meta.get("tokenizer.ggml.model"),
            "pre": meta.get("tokenizer.ggml.pre"),
            "vocab_size": vocab,
        },
        "sampling": sampling,
        "kv_cache": _kv_cache(meta, arch, n_layers),
    }
    if arch == "clip":
        info["projector"] = {
            "vision_dim": _num(meta.get("clip.vision.projection_dim")) if meta.get("clip.has_vision_encoder") else None,
            "audio_dim": _num(meta.get("clip.audio.projection_dim")) if meta.get("clip.has_audio_encoder") else None,
        }
    return info


def find_projectors(model_path: str | Path) -> list[dict[str, Any]]:
    """Multimodal projectors sitting next to a model file."""
    folder = Path(model_path).expanduser().parent
    found = []
    try:
        candidates = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".gguf" and p.is_file())
    except OSError:
        return found
    for p in candidates:
        try:
            info = summarize(p)
        except (OSError, GGUFError):
            continue
        if info["type"] == "mmproj":
            found.append({"path": str(p), "projector": info.get("projector")})
    return found


def trained_context(path: str | Path) -> int | None:
    try:
        return summarize(path)["context_length"]
    except (OSError, GGUFError):
        return None

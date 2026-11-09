"""Identify what kind of OpenAI-compatible server sits behind a remote address,
and read vLLM's Prometheus metrics as a stand-in for llama-server's /slots.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

BackendKind = Literal["llamacpp", "vllm", "openai"]

_TIMEOUT = 5


@dataclass
class BackendInfo:
    kind: BackendKind
    version: str | None = None


async def detect_backend(base_url: str) -> BackendInfo | None:
    """Return the backend type, or None if the server could not be reached.

    llama-server stamps every response with `Server: llama.cpp`; vLLM reports
    `owned_by: "vllm"` in /v1/models and its version at /version. Anything else
    that answers is treated as a generic OpenAI-compatible server.
    """
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            health = await client.get(f"{base}/health")
            if "llama.cpp" in health.headers.get("server", "").lower():
                return BackendInfo("llamacpp", await _llamacpp_version(client, base))

            owned_by: set[str] = set()
            try:
                models = await client.get(f"{base}/v1/models")
                if models.is_success:
                    owned_by = {str(m.get("owned_by", "")).lower() for m in models.json().get("data", [])}
            except (httpx.HTTPError, ValueError, AttributeError):
                pass

            version = None
            try:
                v = await client.get(f"{base}/version")
                if v.is_success:
                    version = v.json().get("version")
            except (httpx.HTTPError, ValueError, AttributeError):
                pass

            if "vllm" in owned_by:
                return BackendInfo("vllm", version)
            return BackendInfo("openai", None)
    except httpx.HTTPError:
        return None


async def _llamacpp_version(client: httpx.AsyncClient, base: str) -> str | None:
    try:
        props = await client.get(f"{base}/props")
        if props.is_success:
            build = props.json().get("build_info")
            return str(build) if build else None
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return None


# ----------------------------------------------------------------------------
# vLLM /metrics
# ----------------------------------------------------------------------------

_SAMPLE_RE = re.compile(r'^(vllm:[a-zA-Z0-9_]+)(\{[^}]*\})?\s+(\S+)$')
_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')

# Rates are computed against the previous scrape; scrapes closer than this
# reuse the last rates instead of producing noisy numbers.
_MIN_RATE_INTERVAL = 1.0


def parse_vllm_metrics(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse Prometheus text into {metric_name: [(labels, value), ...]} for vllm:* samples."""
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for line in text.splitlines():
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name, labels_raw, value = m.groups()
        try:
            v = float(value)
        except ValueError:
            continue
        labels = dict(_LABEL_RE.findall(labels_raw or ""))
        out.setdefault(name, []).append((labels, v))
    return out


def _sum(samples: dict, name: str, **match: str) -> float | None:
    rows = samples.get(name)
    if not rows:
        return None
    return sum(v for labels, v in rows if all(labels.get(k) == want for k, want in match.items()))


@dataclass
class VllmMetricsReader:
    """Turns successive /metrics scrapes into a compact status dict for the UI."""

    _last_t: float | None = None
    _last: dict[str, float] = field(default_factory=dict)
    _rates: dict[str, float | None] = field(default_factory=dict)

    async def read(self, base_url: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{base_url.rstrip('/')}/metrics")
                if not resp.is_success:
                    return None
                text = resp.text
        except httpx.HTTPError:
            return None
        return self._summarise(parse_vllm_metrics(text), time.monotonic())

    def _summarise(self, s: dict, now: float) -> dict | None:
        if not s:
            return None

        counters = {
            "gen": _sum(s, "vllm:generation_tokens_total"),
            "prompt": _sum(s, "vllm:prompt_tokens_total"),
            "ttft_sum": _sum(s, "vllm:time_to_first_token_seconds_sum"),
            "ttft_count": _sum(s, "vllm:time_to_first_token_seconds_count"),
            "prefix_hits": _sum(s, "vllm:prefix_cache_hits_total"),
            "prefix_queries": _sum(s, "vllm:prefix_cache_queries_total"),
        }

        if self._last_t is None or now - self._last_t >= _MIN_RATE_INTERVAL:
            dt = now - self._last_t if self._last_t is not None else None
            self._rates = {
                "gen_tps": self._rate(counters["gen"], "gen", dt),
                "prompt_tps": self._rate(counters["prompt"], "prompt", dt),
                "ttft_recent": self._ratio_delta(counters, "ttft_sum", "ttft_count"),
            }
            self._last_t = now
            self._last = {k: v for k, v in counters.items() if v is not None}

        ttft_avg = (
            counters["ttft_sum"] / counters["ttft_count"]
            if counters["ttft_sum"] is not None and counters["ttft_count"]
            else None
        )
        prefix_hit_rate = (
            counters["prefix_hits"] / counters["prefix_queries"]
            if counters["prefix_hits"] is not None and counters["prefix_queries"]
            else None
        )

        return {
            "requests_running": _sum(s, "vllm:num_requests_running"),
            "requests_waiting": _sum(s, "vllm:num_requests_waiting"),
            "kv_cache_usage": _sum(s, "vllm:kv_cache_usage_perc"),
            "prefix_cache_hit_rate": prefix_hit_rate,
            "generation_tps": self._rates.get("gen_tps"),
            "prompt_tps": self._rates.get("prompt_tps"),
            "ttft_avg": ttft_avg,
            "ttft_recent": self._rates.get("ttft_recent"),
            "prompt_tokens_total": counters["prompt"],
            "generation_tokens_total": counters["gen"],
            "requests_finished": _sum(s, "vllm:request_success_total"),
            "requests_errored": _sum(s, "vllm:request_success_total", finished_reason="error"),
            "preemptions_total": _sum(s, "vllm:num_preemptions_total"),
        }

    def _rate(self, value: float | None, key: str, dt: float | None) -> float | None:
        prev = self._last.get(key)
        if value is None or prev is None or not dt:
            return None
        delta = value - prev
        return delta / dt if delta >= 0 else None  # counter reset (server restart)

    def _ratio_delta(self, counters: dict, num_key: str, den_key: str) -> float | None:
        """Average of a histogram over requests finished since the previous scrape."""
        num, den = counters[num_key], counters[den_key]
        pnum, pden = self._last.get(num_key), self._last.get(den_key)
        if None in (num, den, pnum, pden) or den - pden <= 0:
            return self._rates.get("ttft_recent")  # keep the last value between requests
        return (num - pnum) / (den - pden)

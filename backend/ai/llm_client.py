"""
ai/llm_client.py - Centralized robust LLM interface with caching and streaming.
"""

import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request

import requests

log = logging.getLogger(__name__)

# Configurations
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "").strip()
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
NVIDIA_TIMEOUT_SECONDS = float(os.environ.get("NVIDIA_TIMEOUT_SECONDS", "60"))

LLM_ERROR_PREFIX = "__LLM_ERROR__:"
LLM_UNAVAILABLE_MESSAGE = (
    "Le service IA met trop de temps a repondre pour le moment. "
    "Reessaie dans quelques instants, ou pose une question plus courte."
)


class MemoryCache:
    """Thread-safe in-memory cache for LLM results with Time-To-Live (TTL)."""

    def __init__(self, ttl=86400):
        self._cache = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def get(self, key: str):
        with self._lock:
            if key in self._cache:
                val, expires = self._cache[key]
                if expires > time.time():
                    return val
                del self._cache[key]
            return None

    def set(self, key: str, value: object):
        with self._lock:
            self._cache[key] = (value, time.time() + self._ttl)


_llm_cache = MemoryCache()


def get_messages_hash(messages: list) -> str:
    """Generates an MD5 hash of the LLM messages list."""
    serialized = json.dumps(messages, sort_keys=True)
    return hashlib.md5(serialized.encode("utf-8")).hexdigest()


def call_llm(messages: list, temperature: float = 0.2, max_tokens: int = 1000) -> str:
    """
    Calls the primary LLM (NVIDIA NIM).
    Returns the cached response if available, or calls the API and caches the result.
    """
    key = get_messages_hash(messages)
    cached = _llm_cache.get(key)
    if cached:
        log.info("[LLM] Returning cached response for key %s", key)
        return cached

    if NVIDIA_API_KEY:
        log.info("[LLM] Attempting NVIDIA NIM call...")
        try:
            payload = {
                "model": NVIDIA_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            result = _execute_request(NVIDIA_BASE_URL, NVIDIA_API_KEY, payload)
            if result and not _is_llm_error(result):
                _llm_cache.set(key, result)
            return result
        except (TimeoutError, socket.timeout, urllib.error.URLError, requests.Timeout) as e:
            log.warning("[LLM] NVIDIA NIM timeout/unavailable: %s", e)
            return f"{LLM_ERROR_PREFIX}{LLM_UNAVAILABLE_MESSAGE}"
        except Exception as e:
            log.error("[LLM] NVIDIA NIM call failed: %s", e)
            return f"{LLM_ERROR_PREFIX}Le service IA est temporairement indisponible. Reessaie dans quelques instants."

    log.warning("[LLM] NVIDIA_API_KEY is not configured.")
    return f"{LLM_ERROR_PREFIX}Le service IA n'est pas configure sur ce serveur."


def call_llm_stream(messages: list, temperature: float = 0.2, max_tokens: int = 1000):
    """
    Calls the primary LLM (NVIDIA NIM) and streams the response token by token.
    """
    if NVIDIA_API_KEY:
        log.info("[LLM-stream] Attempting NVIDIA NIM stream...")
        try:
            payload = {
                "model": NVIDIA_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            yield from _execute_request_stream(NVIDIA_BASE_URL, NVIDIA_API_KEY, payload)
            return
        except (TimeoutError, socket.timeout, requests.Timeout) as e:
            log.warning("[LLM-stream] NVIDIA NIM timeout/unavailable: %s", e)
            yield f"\n{LLM_UNAVAILABLE_MESSAGE}"
        except Exception as e:
            log.error("[LLM-stream] NVIDIA NIM stream failed: %s", e)
            yield "\nLe service IA est temporairement indisponible. Reessaie dans quelques instants."
        return

    yield "\nLe service IA n'est pas configure sur ce serveur."


def _is_llm_error(text: str) -> bool:
    return text.startswith(LLM_ERROR_PREFIX)


def strip_llm_error_prefix(text: str) -> str:
    if _is_llm_error(text):
        return text[len(LLM_ERROR_PREFIX):]
    return text


def _execute_request(url: str, api_key: str, payload: dict) -> str:
    """Executes a standard OpenAI-compatible POST request using urllib."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=NVIDIA_TIMEOUT_SECONDS) as resp:
        response_data = json.loads(resp.read().decode("utf-8"))
        return response_data["choices"][0]["message"]["content"].strip()


def _execute_request_stream(url: str, api_key: str, payload: dict):
    """Executes a standard OpenAI-compatible streaming POST request using requests."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    response = requests.post(
        url,
        json=payload,
        headers=headers,
        stream=True,
        timeout=NVIDIA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8").strip()
        if line_str.startswith("data: "):
            data_content = line_str[6:]
            if data_content == "[DONE]":
                break
            try:
                chunk = json.loads(data_content)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
            except Exception as e:
                log.debug("Error parsing stream chunk: %s", e)


def clean_and_parse_json(text: str) -> dict:
    """
    Robustly extracts and parses JSON from LLM outputs.
    Handles wrapping text, markdown code blocks (e.g. ```json ... ```), etc.
    """
    if not text:
        return {}

    cleaned = text.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    log.warning("[LLM] Failed to parse JSON from response: %s", text[:200])
    return {}

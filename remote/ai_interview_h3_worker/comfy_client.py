"""Local-only ComfyUI API client with polling and output download."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable


class ComfyClient:
    def __init__(self, base_url: str, timeout: int = 7200):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request_json(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers={"Content-Type": "application/json"} if payload is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                value = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"ComfyUI API request failed at {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"ComfyUI API returned invalid JSON at {path}")
        return value

    def system_stats(self) -> dict[str, Any]:
        return self.request_json("/system_stats")

    def object_info(self) -> dict[str, Any]:
        return self.request_json("/object_info")

    def submit(self, prompt: dict[str, Any]) -> str:
        value = self.request_json("/prompt", {"prompt": prompt, "client_id": str(uuid.uuid4())})
        prompt_id = value.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError(f"ComfyUI rejected the API workflow: {value}")
        return prompt_id

    def interrupt(self) -> None:
        self.request_json("/interrupt", {})

    def wait(self, prompt_id: str, progress: Callable[[], None] | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            value = self.request_json("/history/" + urllib.parse.quote(prompt_id, safe=""))
            history = value.get(prompt_id)
            if isinstance(history, dict):
                status = history.get("status", {})
                if not isinstance(status, dict):
                    status = {}
                messages = status.get("messages", [])
                if not isinstance(messages, list):
                    messages = []
                execution_error = any(
                    isinstance(message, (list, tuple)) and message and message[0] == "execution_error"
                    for message in messages
                )
                if status.get("status_str") == "error" or execution_error:
                    raise RuntimeError(f"ComfyUI execution failed: {status}")
                if isinstance(status, dict) and status.get("completed") is True:
                    return history
            if progress:
                progress()
            time.sleep(3)
        raise TimeoutError(f"ComfyUI job exceeded {self.timeout}s")

    def download_view(self, item: dict[str, Any], destination: Path) -> None:
        query = urllib.parse.urlencode({
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        })
        request = urllib.request.Request(self.base_url + "/view?" + query)
        try:
            with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as handle:
                while block := response.read(1024 * 1024):
                    handle.write(block)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"Unable to download ComfyUI output: {exc}") from exc

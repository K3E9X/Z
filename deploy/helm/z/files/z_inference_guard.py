"""LiteLLM callback (ADR 0024): refuse model answers that inferX served through an external fallback for C2/C3 keys.

inferX may overflow a request to an external provider ("secours externe"); the answer then carries
`x-inferx-fallback: <provider>`, which LiteLLM returns as `llm_provider-x-inferx-fallback`. The data has already left
by then: this guard cannot prevent that (the inferX project must have no external fallback, ADR 0024), but it makes
it visible and stops the answer from reaching the agent when the mission key's data class is C2 or C3 (a key
without a data class counts as C2). Every fallback is logged on the `z.inference_guard` logger.

Loaded from the LiteLLM config directory: `litellm_settings: {callbacks: z_inference_guard.guard}`. Stdlib, LiteLLM
and FastAPI only (all in the LiteLLM image).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("z.inference_guard")
HEADER = "llm_provider-x-inferx-fallback"
PROTECTED = {"C2", "C3"}


def fallback_of(response: Any) -> str | None:
    hidden = getattr(response, "_hidden_params", None) or {}
    headers = hidden.get("additional_headers") or {}
    for name, value in headers.items():
        if str(name).lower() == HEADER and value:
            return str(value)
    return None


def data_class_of(user_api_key_dict: Any) -> str:
    metadata = getattr(user_api_key_dict, "metadata", None) or {}
    return str(metadata.get("data_class") or "C2")


class ZInferenceGuard(CustomLogger):
    def _check(self, model: Any, user_api_key_dict: Any, response: Any) -> None:
        via = fallback_of(response)
        if via is None:
            return
        data_class = data_class_of(user_api_key_dict)
        mission = (getattr(user_api_key_dict, "metadata", None) or {}).get("mission")
        log.warning(
            "inferX served model %s through the external fallback %s (key data class %s, mission %s)",
            model,
            via,
            data_class,
            mission,
        )
        if data_class in PROTECTED:
            # 403, not 5xx: clients and Z retry 5xx, and a retry would send the same data to the fallback again
            raise HTTPException(
                status_code=403,
                detail=(
                    f"inferX answered through an external fallback ({via}); refused for {data_class} data "
                    "(ADR 0024): the inferX project must not use an external fallback for the models Z calls"
                ),
            )

    async def async_post_call_success_hook(self, data: dict[str, Any], user_api_key_dict: Any, response: Any) -> Any:
        self._check(data.get("model"), user_api_key_dict, response)
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: Any, response: Any, request_data: dict[str, Any]
    ) -> AsyncGenerator[Any, None]:
        # the stream carries the provider's headers before its first chunk: refuse before anything is sent on
        self._check(request_data.get("model"), user_api_key_dict, response)
        async for item in response:
            yield item


guard = ZInferenceGuard()

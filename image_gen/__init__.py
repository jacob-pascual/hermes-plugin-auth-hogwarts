"""Hermes image generation backend for the self-hosted Qwen-Image-2.1 at llm.pascuals.org.

The endpoint accepts only an OIDC access token. The built-in ``openai`` image backend reads a
static key, and such a token expires. This backend asks Hermes for the token of the model
provider ``hogwarts`` (the plugin in the parent directory) before each request; Hermes refreshes
that token.

    hermes auth add hogwarts
    hermes plugins enable image_gen/hogwarts
    hermes config set image_gen.provider hogwarts
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

PROVIDER = "hogwarts"
MODEL = "qwen-image-2.1"
# The sizes from the model card of Qwen-Image-2.1: 16:9, 1:1 and 9:16.
SIZES = {"landscape": "2752x1536", "square": "2048x2048", "portrait": "1536x2752"}
# The GPU holds one model. A request can wait while the server stops the chat model and loads
# the image model; the server gives up after 20 minutes.
TIMEOUT_SECONDS = 1500


# The router rejects a request without these two headers. They are the headers that the
# model provider in the parent directory sends with a chat request.
IDENTITY = {"X-Pascuals-App": "hermes", "X-Pascuals-Host": socket.gethostname().split(".")[0]}


def _endpoint() -> tuple[str, str]:
    """``(base_url, token)`` of the provider, from the resolution that chat uses; ``("", "")``
    when the user is not logged in."""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=PROVIDER)
    except Exception:  # noqa: BLE001 - not logged in, or the provider plugin is not installed
        return "", ""
    return str(runtime.get("base_url") or "").rstrip("/"), str(runtime.get("api_key") or "")


class HogwartsImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Hogwarts (Qwen-Image-2.1)"

    def is_available(self) -> bool:
        return all(_endpoint())

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "id": MODEL,
                "display": "Qwen-Image-2.1",
                "speed": "~110s, more when the server must load the model",
                "strengths": "Text in images, 2K",
                "price": "self-hosted",
            },
        ]

    def default_model(self) -> str | None:
        return MODEL

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "self-hosted",
            "tag": "Qwen-Image-2.1 at llm.pascuals.org; sign in with `hermes auth add hogwarts`",
            # No key to ask for: the login of the model provider is the credential.
            "env_vars": [],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect_ratio = resolve_aspect_ratio(aspect_ratio)
        failure = {"provider": self.name, "model": MODEL, "prompt": prompt, "aspect_ratio": aspect_ratio}
        if not prompt:
            return error_response(error="Prompt is required", error_type="invalid_input", **failure)
        base_url, token = _endpoint()
        if not token:
            return error_response(
                error="Not signed in to llm.pascuals.org. Run `hermes auth add hogwarts`.",
                error_type="auth_required",
                **failure,
            )

        size = SIZES[aspect_ratio]
        request = urllib.request.Request(
            f"{base_url}/images/generations",
            data=json.dumps({"model": MODEL, "prompt": prompt, "size": size, "n": 1}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", **IDENTITY},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                image = json.load(response)["data"][0]["b64_json"]
        except urllib.error.HTTPError as error:
            detail = error.read(500).decode(errors="replace")
            return error_response(error=f"HTTP {error.code}: {detail}", error_type="api_error", **failure)
        except (OSError, ValueError, KeyError, IndexError) as error:
            return error_response(error=str(error), error_type=type(error).__name__, **failure)

        path = save_b64_image(image, prefix=self.name)
        return success_response(
            image=str(path),
            model=MODEL,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            provider=self.name,
            extra={"size": size},
        )


def register(ctx) -> None:
    ctx.register_image_gen_provider(HogwartsImageGenProvider())

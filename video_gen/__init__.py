"""Hermes video generation backend for the self-hosted video models at llm.pascuals.org.

Like the image backend next to it, this asks Hermes for the token of the model provider
``hogwarts`` before each request, so one login covers chat, images and videos.

    hermes auth add hogwarts
    hermes plugins enable video_gen/hogwarts
    hermes tools enable video_gen
    hermes config set video_gen.provider hogwarts
    hermes config set video_gen.model ltx-2.5

The server answers one request with the finished video. It has no job to poll: a request in
progress is what keeps the model on the GPU, because the server holds one model at a time.
"""

from __future__ import annotations

import base64
import json
import socket
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent.video_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    VideoGenProvider,
    error_response,
    save_b64_video,
    success_response,
)

PROVIDER = "hogwarts"
# The GPU holds one model. A request can wait 20 minutes while the server stops the chat model
# and loads the video model. The gateway stops a request that sends no data for one hour.
TIMEOUT_SECONDS = 4800

# ``sizes`` maps (aspect ratio, resolution) to a size that the server accepts for the model.
MODELS: dict[str, dict[str, Any]] = {
    "ltx-2.5": {
        "display": "LTX 2.5",
        "speed": "~2 min for 5 s at 720p, ~5 min for 10 s at 1080p; more after a model swap",
        "strengths": "Sound, image-to-video, up to 1080p and 10 s",
        "modalities": ["text", "image"],
        "min_duration": 2,
        "max_duration": 10,
        "audio": True,
        "sizes": {
            ("16:9", "720p"): "1280x704",
            ("16:9", "1080p"): "1920x1088",
            ("9:16", "720p"): "704x1280",
            ("9:16", "1080p"): "1088x1920",
            ("1:1", "720p"): "1024x1024",
            ("3:2", "720p"): "1536x1024",
            ("2:3", "720p"): "1024x1536",
        },
    },
    "mochi-1-preview": {
        "display": "Mochi 1 preview",
        "speed": "~4 min for each second of video; more after a model swap",
        "strengths": "Motion; text-to-video only, 480p, up to 5 s, no sound",
        "modalities": ["text"],
        "min_duration": 1,
        "max_duration": 5,
        "audio": False,
        "sizes": {("16:9", "480p"): "848x480"},
    },
}
DEFAULT_MODEL = "ltx-2.5"


# The router rejects a request without these two headers. They are the headers that the
# model provider in the parent directory sends with a chat request.
IDENTITY = {"X-Pascuals-App": "hermes", "X-Pascuals-Host": socket.gethostname().split(".")[0]}


def _identity() -> dict[str, str]:
    """``IDENTITY`` plus the Hermes session that asked for this generation, when there is one."""
    try:
        from gateway.session_context import get_session_env

        session = get_session_env("HERMES_SESSION_ID", "")
    except Exception:  # noqa: BLE001 - the header is optional; a generation must not fail for it
        session = ""
    return {**IDENTITY, "X-Pascuals-Session-Id": session} if session else dict(IDENTITY)


def _endpoint() -> tuple[str, str]:
    """``(base_url, token)`` of the provider, from the resolution that chat uses; ``("", "")``
    when the user is not logged in."""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=PROVIDER)
    except Exception:  # noqa: BLE001 - not logged in, or the provider plugin is not installed
        return "", ""
    return str(runtime.get("base_url") or "").rstrip("/"), str(runtime.get("api_key") or "")


def _configured_model() -> str:
    """The model that ``video_gen.model`` selects, when it is one of ours."""
    try:
        from hermes_cli.config import cfg_get, load_config

        model = cfg_get(load_config(), "video_gen", "model")
    except Exception:  # noqa: BLE001 - never break the tool schema over a config read
        model = None
    return model if model in MODELS else DEFAULT_MODEL


def _size(model: str, aspect_ratio: str, resolution: str) -> tuple[str, str]:
    """The size for the request, and the aspect ratio that it has. An aspect ratio or a
    resolution that the model does not have falls back to the nearest one it does."""
    sizes = MODELS[model]["sizes"]
    if (aspect_ratio, resolution) in sizes:
        return sizes[aspect_ratio, resolution], aspect_ratio
    for (ratio, _), size in sizes.items():
        if ratio == aspect_ratio:
            return size, ratio
    (ratio, _), size = next(iter(sizes.items()))
    return size, ratio


def _image(reference: str) -> str:
    """The first frame as a data URL. Hermes passes a URL, a data URL or a local path."""
    if reference.startswith("data:"):
        return reference
    if reference.startswith(("http://", "https://")):
        with urllib.request.urlopen(reference, timeout=60) as response:
            kind = response.headers.get_content_type()
            data = response.read()
    else:
        path = Path(reference.removeprefix("file://")).expanduser()
        kind = mimetypes.guess_type(path.name)[0] or "image/png"
        data = path.read_bytes()
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


class HogwartsVideoGenProvider(VideoGenProvider):
    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Hogwarts (LTX 2.5, Mochi 1)"

    def is_available(self) -> bool:
        return all(_endpoint())

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "id": name,
                "price": "self-hosted",
                **{key: value for key, value in model.items() if key not in ("sizes", "audio")},
            }
            for name, model in MODELS.items()
        ]

    def default_model(self) -> str | None:
        return DEFAULT_MODEL

    def capabilities(self) -> dict[str, Any]:
        # The tool shows these to the agent, thus they are the ones of the configured model.
        model = MODELS[_configured_model()]
        return {
            "modalities": model["modalities"],
            "aspect_ratios": list(dict.fromkeys(ratio for ratio, _ in model["sizes"])),
            "resolutions": list(dict.fromkeys(resolution for _, resolution in model["sizes"])),
            "min_duration": model["min_duration"],
            "max_duration": model["max_duration"],
            # LTX 2.5 always makes sound; the server has no switch for it.
            "supports_audio": False,
            "audio_always_on": model["audio"],
            "supports_negative_prompt": False,
            "supports_seed": True,
            "supports_upscale": False,
            "max_reference_images": 0,
        }

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "self-hosted",
            "tag": "LTX 2.5 and Mochi 1 at llm.pascuals.org; sign in with `hermes auth add hogwarts`",
            # No key to ask for: the login of the model provider is the credential.
            "env_vars": [],
        }

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        image_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        duration: int | None = None,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        resolution: str = DEFAULT_RESOLUTION,
        negative_prompt: str | None = None,
        audio: bool | None = None,
        seed: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        model = model if model in MODELS else _configured_model()
        catalog = MODELS[model]
        size, aspect_ratio = _size(model, aspect_ratio, resolution)
        failure = {"provider": self.name, "model": model, "prompt": prompt, "aspect_ratio": aspect_ratio}
        if not prompt:
            return error_response(error="Prompt is required", error_type="invalid_input", **failure)
        if image_url and "image" not in catalog["modalities"]:
            return error_response(
                error=f"{model} makes a video from a prompt only. Set video_gen.model to ltx-2.5 for image-to-video.",
                error_type="invalid_input",
                **failure,
            )
        base_url, token = _endpoint()
        if not token:
            return error_response(
                error="Not signed in to llm.pascuals.org. Run `hermes auth add hogwarts`.",
                error_type="auth_required",
                **failure,
            )

        body: dict[str, Any] = {"model": model, "prompt": prompt, "size": size}
        if duration:
            body["seconds"] = max(catalog["min_duration"], min(catalog["max_duration"], int(duration)))
        if seed is not None:
            body["seed"] = seed
        try:
            if image_url:
                body["image"] = _image(image_url)
            request = urllib.request.Request(
                f"{base_url}/videos/generations",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", **_identity()},
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                answer = json.load(response)
                video = answer["data"][0]["b64_json"]
        except urllib.error.HTTPError as error:
            detail = error.read(500).decode(errors="replace")
            return error_response(error=f"HTTP {error.code}: {detail}", error_type="api_error", **failure)
        except (OSError, ValueError, KeyError, IndexError) as error:
            return error_response(error=str(error), error_type=type(error).__name__, **failure)

        path = save_b64_video(video, prefix=self.name)
        return success_response(
            video=str(path),
            model=model,
            prompt=prompt,
            modality="image" if image_url else "text",
            aspect_ratio=aspect_ratio,
            duration=round(answer.get("seconds") or 0),
            provider=self.name,
            extra={"size": size, "seed": answer.get("seed"), "fps": answer.get("fps")},
        )


def register(ctx) -> None:
    ctx.register_video_gen_provider(HogwartsVideoGenProvider())

"""Hermes model provider for the self-hosted models at llm.pascuals.org.

Login is browser-based OAuth 2.0 Authorization Code + PKCE against Zitadel at
auth.pascuals.org. Hermes owns the whole lifecycle once this profile is
registered: it opens the browser, validates the PKCE challenge, stores the
tokens, and rotates them with the refresh token before they expire.

    hermes plugins install jacob-pascual/hermes-plugin-auth-hogwarts
    hermes auth add hogwarts

There is no shared secret to distribute. The client below is a public native
client, which is why the id can live in a public repository: PKCE is the
proof, not a secret.
"""

import socket
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

from hermes_cli.auth_oauth_pkce_plugin import (
    OAuthPKCEConfig,
    pkce_auth_handler,
    pkce_refresh_credential,
)

ISSUER = "https://auth.pascuals.org"
API = "https://llm.pascuals.org/v1"

# The router at llm.pascuals.org rejects a request that does not name the program and the host
# it comes from. Its monitor shows both names, so a request can be traced back to the machine
# that sent it; the client address alone cannot do that behind a NAT router. The host is the
# short hostname, without the ".local" that macOS appends.
IDENTITY = {"X-Pascuals-App": "hermes", "X-Pascuals-Host": socket.gethostname().split(".")[0]}

# The access token is verified by Envoy Gateway at the edge, against the JWKS
# this issuer publishes, before any request reaches the inference server. The
# Zitadel app is configured to mint JWT access tokens rather than opaque ones;
# an opaque token cannot be verified offline and is rejected exactly like a
# forged one.
#
# redirect_port is pinned rather than left at 0 (OS-assigned). OAuth redirect
# URIs must match what the provider registered, and Zitadel holds exactly one:
# http://127.0.0.1:8765/callback.
_OAUTH = OAuthPKCEConfig(
    client_id="393751994755448952",
    authorize_url=f"{ISSUER}/oauth/v2/authorize",
    token_url=f"{ISSUER}/oauth/v2/token",
    # offline_access is what makes a refresh token come back; without it the
    # session dies after twelve hours and cannot be renewed silently.
    scopes=("openid", "profile", "email", "offline_access"),
    redirect_port=8765,
    redirect_path="/callback",
    label="pascuals.org (Zitadel)",
)

hogwarts = ProviderProfile(
    name="hogwarts",
    aliases=("pascuals",),
    display_name="Hogwarts",
    description="Self-hosted Qwen3.8 at llm.pascuals.org, behind Zitadel",
    signup_url=ISSUER,
    base_url=API,
    api_mode="chat_completions",
    auth_type="oauth_external",
    # A trailing *_BASE_URL entry lets a user point the same profile at another
    # deployment without editing this plugin.
    env_vars=("HOGWARTS_API_KEY", "HOGWARTS_BASE_URL"),
    default_headers=dict(IDENTITY),
    # Used before the catalogue can be fetched, and if /models is unreachable.
    fallback_models=(
        "qwen3.8-27b",
        "qwen3.8-flash-next",
        "qwen3.8-27b-uncensored",
        "qwen3.8-flash-next-uncensored",
        "glm-5.3-flash",
        "glm-5.3",
    ),
    model_capabilities={
        "qwen3.8-27b": {
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_tools": True,
            # vLLM serves the model with --max-model-len 131072.
            "context_window": 131072,
            "model_family": "qwen",
        },
        "qwen3.8-flash-next": {
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_tools": True,
            # 262k native, and TabbyAPI serves the full window.
            "context_window": 262144,
            "model_family": "qwen",
        },
        # The uncensored versions of the two Qwen models (orcarouter), served next to the Swift
        # versions above. TabbyAPI serves both with the full 262k window.
        "qwen3.8-27b-uncensored": {
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_tools": True,
            "context_window": 262144,
            "model_family": "qwen",
        },
        "qwen3.8-flash-next-uncensored": {
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_tools": True,
            "context_window": 262144,
            "model_family": "qwen",
        },
        # The two GLM models are served with max_seq_len 131072. Most of their experts run on
        # the CPU: expect about 21 tok/s for the Flash model and about 6 tok/s for the full one.
        "glm-5.3-flash": {
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_tools": True,
            "context_window": 131072,
            "model_family": "glm",
        },
        "glm-5.3": {
            "supports_reasoning": True,
            "supports_vision": False,
            "supports_tools": True,
            "context_window": 131072,
            "model_family": "glm",
        },
    },
    auth_handler=pkce_auth_handler(_OAUTH),
    refresh_credential=pkce_refresh_credential(_OAUTH),
)



def _with_session(
    *, reasoning_config: dict | None = None, session_id: str | None = None, **context: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The stock extras, plus the session of the conversation as a header on the chat request.

    ``extra_headers`` is the per-request header argument of the OpenAI client; the bundled
    OpenRouter profile sends ``x-grok-conv-id`` the same way. The monitor of the router shows
    the value in its SESSION column, which ties a request to one Hermes session.
    """
    extra_body, top_level = ProviderProfile.build_api_kwargs_extras(
        hogwarts, reasoning_config=reasoning_config, **context
    )
    if session_id:
        top_level = {**top_level, "extra_headers": {"X-Pascuals-Session-Id": str(session_id)}}
    return extra_body, top_level


# Set on the instance, not in a subclass: the auxiliary client treats a profile whose *class*
# overrides build_api_kwargs_extras as one that handles reasoning itself, and then stops sending
# its generic reasoning fallback. This hook only adds a header, so that fallback must stay.
hogwarts.build_api_kwargs_extras = _with_session

register_provider(hogwarts)

"""Hermes model provider for the self-hosted LLM at llm.pascuals.org.

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

from providers import register_provider
from providers.base import ProviderProfile

from hermes_cli.auth_oauth_pkce_plugin import (
    OAuthPKCEConfig,
    pkce_auth_handler,
    pkce_refresh_credential,
)

ISSUER = "https://auth.pascuals.org"
API = "https://llm.pascuals.org/v1"

# The access token is verified by Envoy Gateway at the edge, against the JWKS
# this issuer publishes, before any request reaches the inference server. The
# Zitadel app is configured to mint JWT access tokens rather than opaque ones;
# an opaque token cannot be verified offline and is rejected exactly like a
# forged one.
#
# redirect_port is pinned rather than left at 0 (OS-assigned). OAuth redirect
# URIs must match what the provider registered, and Zitadel holds a fixed list:
# 8765, then 8080 and 51337 as fallbacks if that port is already in use.
_OAUTH = OAuthPKCEConfig(
    client_id="392117023330533817",
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
    description="Self-hosted Qwen3.8-Flash-Next on ExLlamaV3, behind Zitadel",
    signup_url=ISSUER,
    base_url=API,
    api_mode="chat_completions",
    auth_type="oauth_external",
    # A trailing *_BASE_URL entry lets a user point the same profile at another
    # deployment without editing this plugin.
    env_vars=("HOGWARTS_API_KEY", "HOGWARTS_BASE_URL"),
    # Used before the catalogue can be fetched, and if /models is unreachable.
    fallback_models=("Qwen3.8-Flash-Next-EXL3-3.05bpw",),
    model_capabilities={
        "Qwen3.8-Flash-Next-EXL3-3.05bpw": {
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_tools": True,
            # 262k native. The server is configured for the full window.
            "context_window": 262144,
            "model_family": "qwen",
        },
    },
    auth_handler=pkce_auth_handler(_OAUTH),
    refresh_credential=pkce_refresh_credential(_OAUTH),
)

register_provider(hogwarts)

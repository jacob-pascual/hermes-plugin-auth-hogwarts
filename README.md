# hermes-plugin-auth-hogwarts

A Hermes model provider for the self-hosted LLM at `llm.pascuals.org`.

The endpoint is OpenAI-compatible but not open: Envoy Gateway verifies an
OIDC token on every request before it reaches the inference server. This
plugin teaches Hermes how to get one.

## Install

```sh
hermes plugins install jacob-pascual/hermes-plugin-auth-hogwarts
hermes auth add hogwarts
```

`hermes auth add` opens a browser against `auth.pascuals.org`. After you sign
in, Hermes stores the tokens and refreshes them on its own; there is nothing
to paste and nothing to rotate by hand.

Then point a session at it:

```sh
hermes --provider hogwarts
```

or in `~/.hermes/config.yaml`:

```yaml
model:
  provider: hogwarts
  name: Qwen3.8-Flash-Next-EXL3-3.05bpw
```

## What you need

An account in the `pascuals-infra` Zitadel instance. Ask the operator; there
is no self-signup.

## How it authenticates

OAuth 2.0 Authorization Code with PKCE, against Zitadel:

| | |
|---|---|
| Issuer | `https://auth.pascuals.org` |
| API | `https://llm.pascuals.org/v1` |
| Client | `392117023330533817` — public native client, **no secret** |
| Scopes | `openid profile email offline_access` |
| Redirect | `http://localhost:8765/callback` |

The client id is in this repository on purpose. It is a public client, so
PKCE is the proof of possession rather than a shared secret, and there is
nothing here worth keeping private.

Two details that matter if you fork this for your own deployment:

- **The access token must be a JWT.** Envoy verifies signatures offline
  against the issuer's JWKS, so an opaque token is rejected exactly like a
  forged one. In Zitadel that is `accessTokenType: OIDC_TOKEN_TYPE_JWT` on the
  application.
- **`offline_access` is required.** Without it no refresh token comes back,
  the session dies after twelve hours, and Hermes cannot renew it silently.

The loopback port is pinned to 8765 rather than OS-assigned, because redirect
URIs have to match what the provider registered. 8080 and 51337 are also
registered as fallbacks.

## Pointing it elsewhere

`HOGWARTS_BASE_URL` overrides the endpoint without editing the plugin, which
is useful against a tunnel or a staging deployment.

## Non-interactive use

For CI or a daemon, skip this plugin. Use a Zitadel machine user with
`client_credentials` and Hermes's `key_cmd`, which re-mints a token before it
expires:

```yaml
providers:
  hogwarts-machine:
    api: https://llm.pascuals.org/v1
    key_cmd: "/path/to/llm-token.sh"
    transport: chat_completions
```

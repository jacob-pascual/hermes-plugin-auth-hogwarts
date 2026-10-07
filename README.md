# hermes-plugin-auth-hogwarts

A Hermes model provider, image generation backend and video generation backend
for the self-hosted models at `llm.pascuals.org`.

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

or make it the default:

```sh
hermes config set model.provider hogwarts
hermes config set model.default qwen3.8-27b
```

## Image generation

`image_gen/` is a second plugin: an image backend for `qwen-image-2.1` on the
same endpoint. Hermes' built-in `openai` image backend can point at a custom
URL, but it only reads a static key, and this endpoint only takes the OIDC
token. The backend here asks Hermes for the token of the `hogwarts` provider
before each request, so one login covers both.

`hermes plugins install` clones this repository to `~/.hermes/plugins/hogwarts`.
Image backends are discovered one level down, so link the subdirectory there:

```sh
mkdir -p ~/.hermes/plugins/image_gen
ln -s ~/.hermes/plugins/hogwarts/image_gen ~/.hermes/plugins/image_gen/hogwarts
hermes plugins enable image_gen/hogwarts
hermes config set image_gen.provider hogwarts
```

The server holds one model on its GPU at a time. An image request made from a
chat stops the chat model, loads the image model, and the next chat turn swaps
back: expect about six minutes for one 2048x2048 image inside a conversation.

## Video generation

`video_gen/` is a third plugin: a backend for Hermes' `video_generate` tool,
for the video models on the same endpoint. It uses the token of the `hogwarts`
provider, like the image backend.

```sh
mkdir -p ~/.hermes/plugins/video_gen
ln -s ~/.hermes/plugins/hogwarts/video_gen ~/.hermes/plugins/video_gen/hogwarts
hermes plugins enable video_gen/hogwarts
hermes tools enable video_gen
hermes config set video_gen.provider hogwarts
hermes config set video_gen.model ltx-2.5
```

`hermes tools enable video_gen` matters: the video toolset is off by default,
and without it the agent has no `video_generate` tool at all.

| `video_gen.model` | Input | Output | Time on the server |
|---|---|---|---|
| `ltx-2.5` | Prompt, optional first frame | 720p or 1080p, 2 to 10 s, with sound | About 2 minutes for 5 s at 720p |
| `mochi-1-preview` | Prompt | 848x480, 1 to 5 s, no sound | About 4 minutes for each second of video |

The agent does not pick the model: `video_gen.model` does. The server answers
one request with the finished video, and that request is what keeps the model
on the GPU, so there is no job to poll.

Hermes stops a tool call after 420 seconds by default. A video takes longer
than that when the server must first swap models, and Mochi always does:

```sh
hermes config set timeouts.tools.sequential_call 4800
hermes config set timeouts.tools.concurrent_batch 4800
```

## What you need

An account in the `pascuals-infra` Zitadel instance with the role `hermes` in
the project `llm`. Ask the operator; there is no self-signup, and an account
without the role is refused at login.

## How it authenticates

OAuth 2.0 Authorization Code with PKCE, against Zitadel:

| | |
|---|---|
| Issuer | `https://auth.pascuals.org` |
| API | `https://llm.pascuals.org/v1` |
| Client | `393751994755448952` — public native client, **no secret** |
| Scopes | `openid profile email offline_access` |
| Redirect | `http://127.0.0.1:8765/callback` |

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
URIs have to match what the provider registered.

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

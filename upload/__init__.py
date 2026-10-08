"""Hermes tool ``upload_file``: put a local file in temporary storage and get a signed URL.

Many tools take only a public URL, for example ``image_url`` of ``video_generate``. This tool
uploads a local file to the bucket ``uploads`` at s3.pascuals.org (Ceph RGW) and returns a URL
that is signed, so anyone who has the URL can read the file until the URL expires. The storage
deletes the file after one day.

There is no S3 key to configure. The tool sends the token of the model provider ``hogwarts``
to the STS call of the storage (``AssumeRoleWithWebIdentity``) and gets keys that live as long
as the URL. So the login of the provider covers the uploads too:

    hermes auth add hogwarts
    hermes plugins enable hogwarts-upload
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import http.client
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree
from pathlib import Path
from typing import Any

PROVIDER = "hogwarts"
HOST = "s3.pascuals.org"
BUCKET = "uploads"
ROLE = "arn:aws:iam:::role/hermes-upload"
# Ceph accepts each region name; the signature only has to name one.
REGION = "us-east-1"
# The storage gives keys for 15 minutes to 12 hours, and a signed URL dies with its keys.
MIN_SECONDS, DEFAULT_SECONDS, MAX_SECONDS = 900, 3600, 43200
MAX_BYTES = 1 << 30

SCHEMA = {
    "name": "upload_file",
    "description": (
        "Upload a local file to temporary storage and get a signed HTTPS URL for it. Use this "
        "when a tool needs a public URL for a local file, for example `image_url` of "
        "`video_generate`. Anyone who has the URL can read the file until the URL expires. "
        "The storage deletes the file after one day."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the local file to upload."},
            "expires_seconds": {
                "type": "integer",
                "description": (
                    f"How long the URL stays valid, {MIN_SECONDS} to {MAX_SECONDS} seconds. "
                    f"Default {DEFAULT_SECONDS}."
                ),
            },
        },
        "required": ["path"],
    },
}


def _token() -> str:
    """The access token of the provider, from the resolution that chat uses; ``""`` when the
    user is not logged in."""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        return str(resolve_runtime_provider(requested=PROVIDER).get("api_key") or "")
    except Exception:  # noqa: BLE001 - not logged in, or the provider plugin is not installed
        return ""


def _keys(token: str, seconds: int) -> dict[str, str]:
    """Exchange the OIDC token for temporary S3 keys of the upload role."""
    form = {
        "Action": "AssumeRoleWithWebIdentity",
        "Version": "2011-06-15",
        "RoleArn": ROLE,
        "RoleSessionName": "hermes",
        "DurationSeconds": seconds,
        "WebIdentityToken": token,
    }
    request = urllib.request.Request(f"https://{HOST}/", data=urllib.parse.urlencode(form).encode())
    with urllib.request.urlopen(request, timeout=60) as response:
        document = xml.etree.ElementTree.fromstring(response.read())
    # The answer is XML with a namespace; only these three leaves matter.
    found = {element.tag.rsplit("}", 1)[-1]: element.text or "" for element in document.iter()}
    return {name: found[name] for name in ("AccessKeyId", "SecretAccessKey", "SessionToken")}


def _quote(text: str) -> str:
    return urllib.parse.quote(text, safe="-_.~")


def _signature(
    keys: dict[str, str], now: datetime.datetime, method: str, path: str, query: str, headers: str, body: str
) -> str:
    """AWS Signature Version 4. ``query`` and ``headers`` are already in canonical form, and
    every header in ``headers`` is signed. ``body`` is the SHA-256 of the body as hex, or
    ``UNSIGNED-PAYLOAD``."""
    date, stamp = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    names = ";".join(line.split(":", 1)[0] for line in headers.splitlines())
    request = "\n".join([method, path, query, headers, "", names, body])
    scope = f"{date}/{REGION}/s3/aws4_request"
    text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest()])
    key = ("AWS4" + keys["SecretAccessKey"]).encode()
    for part in (date, REGION, "s3", "aws4_request", text):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key.hex()


def _put(path: Path, target: str, kind: str, keys: dict[str, str]) -> None:
    """Upload the file with a signed PUT. The file is streamed, not read into memory."""
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    now = datetime.datetime.now(datetime.timezone.utc)
    # In the order of their names. Ceph refuses a request whose content-type or x-amz-*
    # headers are not signed.
    signed = {
        "content-type": kind,
        "host": HOST,
        "x-amz-content-sha256": digest,
        "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ"),
        "x-amz-security-token": keys["SessionToken"],
    }
    canonical = "\n".join(f"{name}:{value}" for name, value in signed.items())
    signature = _signature(keys, now, "PUT", target, "", canonical, digest)
    credential = f"{keys['AccessKeyId']}/{now:%Y%m%d}/{REGION}/s3/aws4_request"
    headers = {
        **signed,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={credential}, SignedHeaders={';'.join(signed)}, Signature={signature}",
        "Content-Length": str(path.stat().st_size),
    }
    connection = http.client.HTTPSConnection(HOST, timeout=600)
    try:
        with path.open("rb") as file:
            connection.request("PUT", target, body=file, headers=headers)
            response = connection.getresponse()
            detail = response.read(500).decode(errors="replace")
        if response.status != 200:
            raise OSError(f"the storage answered HTTP {response.status}: {detail}")
    finally:
        connection.close()


def _signed_url(target: str, keys: dict[str, str], seconds: int) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    parameters = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{keys['AccessKeyId']}/{now:%Y%m%d}/{REGION}/s3/aws4_request",
        "X-Amz-Date": now.strftime("%Y%m%dT%H%M%SZ"),
        "X-Amz-Expires": str(seconds),
        "X-Amz-Security-Token": keys["SessionToken"],
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(f"{_quote(name)}={_quote(value)}" for name, value in sorted(parameters.items()))
    signature = _signature(keys, now, "GET", target, query, f"host:{HOST}", "UNSIGNED-PAYLOAD")
    return f"https://{HOST}{target}?{query}&X-Amz-Signature={signature}"


def _failure(error: str) -> str:
    return json.dumps({"success": False, "error": error})


def _upload(arguments: dict[str, Any], **_kwargs: Any) -> str:
    raw = str(arguments.get("path") or "").strip()
    if not raw:
        return _failure("path is required")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        return _failure(f"{path} is not a file")
    # The same rule as the read tool: no credential stores, no .env files.
    from agent.file_safety import get_read_block_error

    blocked = get_read_block_error(str(path))
    if blocked:
        return _failure(blocked)
    size = path.stat().st_size
    if size > MAX_BYTES:
        return _failure(f"{path.name} has {size} bytes; the limit is {MAX_BYTES}")
    try:
        seconds = int(arguments.get("expires_seconds") or DEFAULT_SECONDS)
    except (TypeError, ValueError):
        return _failure("expires_seconds must be a number")
    seconds = max(MIN_SECONDS, min(MAX_SECONDS, seconds))
    token = _token()
    if not token:
        return _failure("Not signed in to pascuals.org. Run `hermes auth add hogwarts`.")

    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # A random folder makes the address impossible to guess; the name keeps its extension.
    target = f"/{BUCKET}/{uuid.uuid4().hex}/{_quote(path.name)}"
    try:
        keys = _keys(token, seconds)
        _put(path, target, kind, keys)
        url = _signed_url(target, keys, seconds)
    except urllib.error.HTTPError as error:
        return _failure(f"the storage refused the login: HTTP {error.code}: {error.read(300).decode(errors='replace')}")
    except (OSError, KeyError, xml.etree.ElementTree.ParseError) as error:
        return _failure(str(error))
    return json.dumps(
        {
            "success": True,
            "url": url,
            "expires_in_seconds": seconds,
            "bytes": size,
            "content_type": kind,
            "note": "Anyone who has the URL can read the file. The storage deletes the file after one day.",
        }
    )


def register(ctx) -> None:
    ctx.register_tool(name="upload_file", toolset="upload", schema=SCHEMA, handler=_upload)

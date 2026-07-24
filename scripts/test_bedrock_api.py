#!/usr/bin/env python3
"""Smoke-test AWS Bedrock API Key (Bearer / ABSK), matching OpenJellyfish auth.

Auth (same as app/services/bedrock.py):
  Authorization: Bearer $BEDROCK_API_KEY
  POST https://bedrock-runtime.{region}.amazonaws.com/model/{profile}/invoke

Usage:
  export BEDROCK_API_KEY=ABSKxxxx   # or AWS_BEARER_TOKEN_BEDROCK
  export BEDROCK_REGION=us-east-1   # optional, default us-east-1

  # from repo root, with project venv:
  ./venv/bin/python scripts/test_bedrock_api.py
  ./venv/bin/python scripts/test_bedrock_api.py --model claude-haiku-4-5
  ./venv/bin/python scripts/test_bedrock_api.py --list-models
  ./venv/bin/python scripts/test_bedrock_api.py --key ABSK... --region us-west-2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Short name → profile id without geo prefix (keep in sync with app/services/bedrock.py)
INFERENCE_PROFILE_MAP = {
    "claude-opus-4-7": "anthropic.claude-opus-4-7",
    "claude-opus-4-6": "anthropic.claude-opus-4-6-v1",
    "claude-opus-4-5": "anthropic.claude-opus-4-5-20251101-v1:0",
    "claude-opus-4-1": "anthropic.claude-opus-4-1-20250805-v1:0",
    "claude-sonnet-4-6": "anthropic.claude-sonnet-4-6",
    "claude-sonnet-4-5": "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-haiku-4-5": "anthropic.claude-haiku-4-5-20251001-v1:0",
}
_GEO_PREFIXES = ("us.", "eu.", "apac.", "jp.", "au.", "global.")

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_REGION = "us-east-1"


def geo_prefix_for_region(region: str) -> str:
    r = (region or DEFAULT_REGION).strip().lower()
    if r.startswith(("us-", "us_", "ca-", "ca_")) or r in ("us", "ca"):
        return "us"
    if r.startswith(("ap-", "ap_", "apac")):
        return "apac"
    if r.startswith(("eu-", "eu_", "eusc-", "eusc_")) or r in ("eu",):
        return "eu"
    if r.startswith(("sa-", "sa_")):
        return "us"
    return "us"


def _strip_geo_prefix(model_id: str) -> str:
    for p in _GEO_PREFIXES:
        if model_id.startswith(p):
            return model_id[len(p):]
    return model_id


def _resolve_key(cli_key: str | None) -> str:
    key = (
        (cli_key or "").strip()
        or os.getenv("BEDROCK_API_KEY", "").strip()
        or os.getenv("AWS_BEARER_TOKEN_BEDROCK", "").strip()
    )
    if not key:
        print(
            "ERROR: missing API key. Set BEDROCK_API_KEY (or AWS_BEARER_TOKEN_BEDROCK), "
            "or pass --key.",
            file=sys.stderr,
        )
        sys.exit(2)
    return key


def _to_model_id(name: str, region: str) -> str:
    geo = geo_prefix_for_region(region)
    if name in INFERENCE_PROFILE_MAP:
        return f"{geo}.{_strip_geo_prefix(INFERENCE_PROFILE_MAP[name])}"
    if "anthropic." in name:
        bare = _strip_geo_prefix(name)
        if bare.startswith("anthropic."):
            return f"{geo}.{bare}"
        return name
    return name


def _request(
    method: str,
    url: str,
    *,
    api_key: str,
    body: dict | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return e.code, err_body
    except urllib.error.URLError as e:
        print(f"ERROR: network failure: {e.reason}", file=sys.stderr)
        sys.exit(1)


def list_foundation_models(api_key: str, region: str) -> int:
    """List foundation models via control-plane API (auth check + visibility)."""
    url = f"https://bedrock.{region}.amazonaws.com/foundation-models"
    print(f"→ GET {url}")
    code, text = _request("GET", url, api_key=api_key, timeout=30.0)
    if code != 200:
        print(f"FAIL HTTP {code}\n{text}")
        return 1
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print(f"FAIL: non-JSON response\n{text[:500]}")
        return 1
    models = payload.get("modelSummaries") or payload.get("models") or []
    print(f"OK  listed {len(models)} foundation model(s) in {region}")
    # Show a few Anthropic rows if present
    anth = [
        m
        for m in models
        if "anthropic" in str(m.get("providerName", "")).lower()
        or "anthropic" in str(m.get("modelId", "")).lower()
    ]
    for m in anth[:8]:
        mid = m.get("modelId") or m.get("modelArn") or "?"
        name = m.get("modelName") or ""
        print(f"  - {mid}  {name}")
    if len(anth) > 8:
        print(f"  … +{len(anth) - 8} more anthropic models")
    return 0


def invoke_chat(api_key: str, region: str, model: str, prompt: str) -> int:
    model_id = _to_model_id(model, region)
    encoded = urllib.parse.quote(model_id, safe="")
    url = (
        f"https://bedrock-runtime.{region}.amazonaws.com"
        f"/model/{encoded}/invoke"
    )
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": prompt}],
    }
    # Opus 4.7 rejects temperature; others can take a small default
    if "opus-4-7" not in model and "opus-4-7" not in model_id:
        body["temperature"] = 0.0

    print(f"→ POST {url}")
    print(f"  model short={model!r}  profile={model_id!r}  region={region}")
    code, text = _request("POST", url, api_key=api_key, body=body, timeout=90.0)
    if code != 200:
        print(f"FAIL HTTP {code}\n{text}")
        if code == 401:
            print("hint: key invalid/expired, or not a Bedrock Bearer (ABSK) token", file=sys.stderr)
        elif code == 403:
            print("hint: key OK shape but no permission for this model/region", file=sys.stderr)
        elif code == 404:
            print("hint: wrong inference profile id or model not enabled in this region", file=sys.stderr)
        return 1

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print(f"FAIL: non-JSON response\n{text[:800]}")
        return 1

    # Anthropic Messages response on Bedrock
    parts = payload.get("content") or []
    texts = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            texts.append(p.get("text") or "")
    reply = "".join(texts).strip() or json.dumps(payload, ensure_ascii=False)[:500]
    usage = payload.get("usage") or {}
    stop = payload.get("stop_reason")
    print("OK  invoke succeeded")
    if usage:
        print(f"  usage: {usage}")
    if stop:
        print(f"  stop_reason: {stop}")
    print("--- assistant ---")
    print(reply)
    print("-----------------")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Test AWS Bedrock Bearer API key")
    p.add_argument("--key", help="Bedrock API key (overrides env)")
    p.add_argument(
        "--region",
        default=os.getenv("BEDROCK_REGION", DEFAULT_REGION).strip() or DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            "Short name or full inference profile id "
            f"(default: {DEFAULT_MODEL}). Short names: "
            + ", ".join(INFERENCE_PROFILE_MAP)
        ),
    )
    p.add_argument(
        "--prompt",
        default="Reply with exactly: bedrock-ok",
        help="User prompt for the smoke invoke",
    )
    p.add_argument(
        "--list-models",
        action="store_true",
        help="Only list foundation models (control plane); skip invoke",
    )
    p.add_argument(
        "--also-list",
        action="store_true",
        help="After a successful invoke, also list foundation models",
    )
    args = p.parse_args()

    api_key = _resolve_key(args.key)
    masked = api_key[:6] + "…" + api_key[-4:] if len(api_key) > 12 else "(short)"
    print(f"key={masked}  region={args.region}")

    if args.list_models:
        return list_foundation_models(api_key, args.region)

    rc = invoke_chat(api_key, args.region, args.model, args.prompt)
    if rc == 0 and args.also_list:
        print()
        list_foundation_models(api_key, args.region)
    return rc


if __name__ == "__main__":
    sys.exit(main())

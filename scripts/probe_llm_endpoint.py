#!/usr/bin/env python3
"""Verify an LLM endpoint actually returns text, and diagnose URL/model shape.

A reachable endpoint is not a working one. `GET /models` answers from local
gateway config, a 200 can carry zero choices, and a reasoning model can burn the
whole budget and return empty content — all of which look healthy right up to
the first real task. This probe therefore requires a **non-empty text reply**.

When the configured shape fails it retries the neighbouring shapes (with/without
the `/v1` suffix, with/without a `provider/` model prefix, `/v1/messages` for
Anthropic) and, if one of those works, prints the exact edit to make.

Exit: 0 PASS, 1 FAIL, 77 SKIP (unfilled placeholder or transient gateway).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

PLACEHOLDERS = {"", "human", "none", "null", "todo", "<your_key>", "<your_api_key>"}
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 530}
UA = "curl/8.5.0"


def is_placeholder(value: str) -> bool:
    return (value or "").strip().lower() in PLACEHOLDERS


def post(url: str, payload: dict, headers: dict, timeout: int):
    """-> (status, parsed_body_or_None, raw_text, server_header, error_kind)"""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA, **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            server = (r.headers.get("server") or "").lower()
            try:
                return r.status, json.loads(raw), raw, server, None
            except json.JSONDecodeError:
                return r.status, None, raw, server, "invalid_json"
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        server = (e.headers.get("server") or "").lower() if e.headers else ""
        return e.code, None, raw, server, "http_error"
    except Exception as e:  # socket timeouts, DNS, TLS, refused
        return None, None, "", "", f"{type(e).__name__}: {e}"


def openai_text(body) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] or {}
    message = first.get("message") or {}
    parts = [message.get("content"), message.get("reasoning_content"), first.get("text")]
    for part in parts:
        if isinstance(part, str) and part.strip():
            return part.strip()
        if isinstance(part, list):  # content blocks
            joined = "".join(
                b.get("text", "") for b in part if isinstance(b, dict)
            ).strip()
            if joined:
                return joined
    return ""


def anthropic_text(body) -> str:
    if not isinstance(body, dict):
        return ""
    content = body.get("content")
    if isinstance(content, list):
        joined = "".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        ).strip()
        if joined:
            return joined
    return ""


def base_variants(base: str):
    """Configured base first, then the shapes people actually mistype."""
    base = (base or "").rstrip("/")
    out = [base]
    for suffix in ("/chat/completions", "/completions", "/messages"):
        if base.endswith(suffix):
            out.append(base[: -len(suffix)].rstrip("/"))
    trimmed = out[-1]
    if not trimmed.endswith("/v1"):
        out.append(trimmed + "/v1")
    else:
        out.append(trimmed[: -len("/v1")].rstrip("/"))
    seen, uniq = set(), []
    for candidate in out:
        if candidate and candidate not in seen:
            seen.add(candidate)
            uniq.append(candidate)
    return uniq


def model_variants(model: str):
    model = (model or "").strip()
    out = [model]
    if "/" in model:
        out.append(model.split("/", 1)[1])
    else:
        out.append("openai/" + model)
    return [m for i, m in enumerate(out) if m and m not in out[:i]]


def try_openai(base: str, model: str, key: str, timeout: int, max_tokens: int):
    status, body, raw, server, err = post(
        f"{base}/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
         "max_tokens": max_tokens},
        {"Authorization": f"Bearer {key}"} if key else {},
        timeout,
    )
    text = openai_text(body)
    return {"status": status, "text": text, "raw": raw[:400], "server": server,
            "err": err, "base": base, "model": model}


def try_anthropic(base: str, model: str, key: str, timeout: int, max_tokens: int):
    root = base.rstrip("/")
    path = "/messages" if root.endswith("/v1") else "/v1/messages"
    status, body, raw, server, err = post(
        f"{root}{path}",
        {"model": model, "max_tokens": max_tokens,
         "messages": [{"role": "user", "content": "Reply with the single word: pong"}]},
        {"x-api-key": key, "anthropic-version": "2023-06-01"} if key else
        {"anthropic-version": "2023-06-01"},
        timeout,
    )
    text = anthropic_text(body)
    return {"status": status, "text": text, "raw": raw[:400], "server": server,
            "err": err, "base": root + path, "model": model}


def transient(result) -> bool:
    return result["err"] not in (None, "invalid_json", "http_error") or (
        result["status"] in TRANSIENT_HTTP
    )


def describe(result) -> str:
    if result["status"] is None:
        return f"unreachable ({result['err']})"
    if result["text"]:
        return f"HTTP {result['status']}, text OK"
    if result["status"] == 200:
        return f"HTTP 200 but no text in the reply ({result['err'] or 'empty content'})"
    return f"HTTP {result['status']}"


def probe(kind, base, model, key, attempts, timeout, max_tokens, verbose):
    """Try the configured shape, then neighbours. -> (ok, result, fix|None)"""
    attempt_fn = try_openai if kind == "openai" else try_anthropic
    configured = None
    for attempt in range(1, attempts + 1):
        result = attempt_fn(base, model, key, timeout, max_tokens)
        configured = configured or result
        if result["text"]:
            return True, result, None
        if verbose:
            print(f"INFO: attempt {attempt}/{attempts} — {describe(result)}", file=sys.stderr)
        if not transient(result):
            configured = result
            break
        configured = result
        if attempt < attempts:
            time.sleep(min(2 ** attempt, 8))

    for cand_base in base_variants(base):
        for cand_model in model_variants(model):
            if cand_base == base.rstrip("/") and cand_model == model:
                continue
            result = attempt_fn(cand_base, cand_model, key, timeout, max_tokens)
            if result["text"]:
                fix = []
                if cand_base != base.rstrip("/"):
                    fix.append(f"api_base_url: {base}  ->  {cand_base}")
                if cand_model != model:
                    fix.append(f"model: {model}  ->  {cand_model}")
                return False, configured, fix
    return False, configured, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("PROBE_BASE_URL", ""))
    ap.add_argument("--model", default=os.environ.get("PROBE_MODEL", ""))
    ap.add_argument("--api-key", default=os.environ.get("PROBE_API_KEY", ""))
    ap.add_argument("--anthropic-base-url", default=os.environ.get("PROBE_ANTHROPIC_BASE_URL", ""),
                    help="Also probe the Anthropic Messages path against this root")
    ap.add_argument("--anthropic-model", default=os.environ.get("PROBE_ANTHROPIC_MODEL", ""))
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=32)
    ap.add_argument("--label", default="LLM endpoint")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if is_placeholder(args.base_url) or is_placeholder(args.model):
        print(f"SKIP: {args.label} not filled in yet (api_base_url/model still a placeholder)")
        return 77

    ok, result, fix = probe("openai", args.base_url.rstrip("/"), args.model, args.api_key,
                            args.attempts, args.timeout, args.max_tokens, args.verbose)
    if ok:
        preview = result["text"].replace("\n", " ")[:60]
        print(f"PASS: {args.label} returned text ({result['base']}, model={result['model']}): {preview!r}")
    elif transient(result) and not fix:
        print(f"SKIP: {args.label} looks transiently unavailable, not misconfigured "
              f"— {describe(result)}")
        return 77
    else:
        print(f"FAIL: {args.label} did not return text — {describe(result)}")
        if result["raw"]:
            print(f"      body: {result['raw']}")
        if fix:
            print("FIX:  a neighbouring shape works — apply to config.yaml:")
            for line in fix:
                print(f"      {line}")
        else:
            print("      no neighbouring URL/model shape worked either; check the key, "
                  "the served model name, and that the gateway is up")
        return 1

    if args.anthropic_base_url and not is_placeholder(args.anthropic_base_url):
        model = args.anthropic_model or args.model
        ok_a, result_a, fix_a = probe("anthropic", args.anthropic_base_url, model, args.api_key,
                                      args.attempts, args.timeout, args.max_tokens, args.verbose)
        if ok_a:
            print(f"PASS: Anthropic path returned text ({result_a['base']}, model={result_a['model']})")
        else:
            print(f"FAIL: Anthropic path did not return text — {describe(result_a)} at {result_a['base']}")
            if fix_a:
                print("FIX:  a neighbouring shape works — apply to config.yaml:")
                for line in fix_a:
                    print(f"      {line}")
            else:
                print("      anthropic_base_url must be the provider's Anthropic root "
                      "(no /v1) in native mode, or the local proxy URL in openai_proxy mode")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

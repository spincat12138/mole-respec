"""PushPlus notification client."""

from __future__ import annotations

import http.client
import json


PUSHPLUS_HOST = "www.pushplus.plus"
PUSHPLUS_PATH = "/send"


def send_pushplus(token: str, title: str, content: str) -> None:
    """Send one Markdown notification and raise on transport/API errors."""
    normalized_token = token.strip()
    if not normalized_token:
        raise ValueError("PushPlus token 不能为空")

    payload = json.dumps(
        {
            "token": normalized_token,
            "title": title,
            "content": content,
            "channel": "qq",
            "option": "",
            "template": "markdown",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    connection = http.client.HTTPSConnection(PUSHPLUS_HOST, timeout=10)
    try:
        connection.request(
            "POST",
            PUSHPLUS_PATH,
            payload,
            {"Content-Type": "application/json; charset=utf-8"},
        )
        response = connection.getresponse()
        raw_body = response.read().decode("utf-8", errors="replace")
    finally:
        connection.close()

    if response.status < 200 or response.status >= 300:
        raise ConnectionError(f"PushPlus HTTP {response.status}: {raw_body}")
    try:
        result = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ConnectionError("PushPlus 返回了无法解析的响应") from exc
    if not isinstance(result, dict) or result.get("code") not in (200, "200"):
        raise ConnectionError(f"PushPlus 推送失败：{raw_body}")

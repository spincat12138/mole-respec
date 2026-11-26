import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from gui import build_wash_notification_metadata
from pushplus import send_pushplus


class FakeResponse:
    def __init__(self, status=200, body='{"code":200}'):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body


class PushPlusTests(unittest.TestCase):
    def test_notification_metadata_uses_shared_template(self):
        started_at = datetime(2026, 11, 25, 8, 9, 10, 123000, timezone(timedelta(hours=8)))
        stopped_at = datetime(2026, 11, 25, 8, 10, 11, 456000, timezone(timedelta(hours=8)))
        self.assertEqual(
            build_wash_notification_metadata(
                "小蓝",
                "水蓝鼠",
                12,
                "大丸子",
                34,
                started_at,
                stopped_at,
                False,
            ),
            (
                "**宠物昵称：** 小蓝",
                "**宠物类型：** 水蓝鼠",
                "**洗宠次数：** 12次",
                "**剩余道具：** 大丸子 34个",
                "**开始时间：** 2026-11-25 08:09:10.123",
                "**停止时间：** 2026-11-25 08:10:11.456",
                "**结果：** 未达到预期目标",
            ),
        )

    def test_notification_times_make_repeated_results_unique(self):
        started_at = datetime(2026, 11, 25, 8, 9, 10, 123000)
        first = build_wash_notification_metadata(
            "小蓝", "水蓝鼠", 12, "大丸子", 34, started_at, started_at, True
        )
        second = build_wash_notification_metadata(
            "小蓝",
            "水蓝鼠",
            12,
            "大丸子",
            34,
            started_at + timedelta(milliseconds=1),
            started_at + timedelta(milliseconds=1),
            True,
        )
        self.assertNotEqual(first, second)

    @mock.patch("pushplus.http.client.HTTPSConnection")
    def test_send_pushplus_posts_markdown_payload(self, connection_class):
        connection = connection_class.return_value
        connection.getresponse.return_value = FakeResponse()

        send_pushplus(" token ", "标题", "正文")

        connection.request.assert_called_once()
        method, path, payload, headers = connection.request.call_args.args
        self.assertEqual((method, path), ("POST", "/send"))
        self.assertIsInstance(payload, bytes)
        self.assertEqual(json.loads(payload.decode("utf-8")), {
            "token": "token",
            "title": "标题",
            "content": "正文",
            "channel": "qq",
            "option": "",
            "template": "markdown",
        })
        self.assertEqual(headers, {"Content-Type": "application/json; charset=utf-8"})
        connection.close.assert_called_once()

    @mock.patch("pushplus.http.client.HTTPSConnection")
    def test_send_pushplus_raises_for_api_failure(self, connection_class):
        connection = connection_class.return_value
        connection.getresponse.return_value = FakeResponse(
            body='{"code":400,"msg":"bad token"}'
        )

        with self.assertRaisesRegex(ConnectionError, "推送失败"):
            send_pushplus("token", "标题", "正文")


if __name__ == "__main__":
    unittest.main()

"""Tests for gtasks.transport: URL/params/body building, retries and error mapping."""

import json
import unittest
import urllib.error
from http.client import RemoteDisconnected

from gtasks.errors import ApiError, AuthError, GtasksError, NotFoundError
from gtasks.transport import HttpTransport

from tests.support import (
    FakeResponse,
    api_error_response,
    http_error,
    json_response,
    patch_sleep,
    patch_urlopen,
    request_json,
    sleep_args,
    split_url,
)


class _AccessToken(str):
    """A string that also supports being called, so either transport style works."""

    def __call__(self):
        return str(self)


class FakeCredentials:
    def __init__(self, token="tok"):
        self.access_token = _AccessToken(token)
        self.token = None


def make_transport(responses, *, credentials=None, **kwargs):
    transport = HttpTransport(credentials or FakeCredentials(), **kwargs)
    return transport, responses


class RequestBuildingTests(unittest.TestCase):
    def test_get_url_headers_and_timeout(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [json_response({"ok": True})]) as recorder:
            result = transport.request("GET", "/users/@me/lists")
        self.assertEqual(result, {"ok": True})
        request = recorder.requests[0]
        parts, params = split_url(request.url)
        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.netloc, "tasks.googleapis.com")
        self.assertEqual(parts.path, "/tasks/v1/users/@me/lists")
        self.assertEqual(params, {})
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.headers.get("authorization"), "Bearer tok")
        self.assertEqual(request.headers.get("accept"), "application/json")
        self.assertEqual(request.headers.get("user-agent"), "gtasks/0.1.1")
        self.assertNotIn("content-type", request.headers)
        self.assertEqual(request.timeout, 30.0)

    def test_custom_base_url_and_timeout(self):
        transport = HttpTransport(
            FakeCredentials(), base_url="https://example.test/api/", timeout=5.5
        )
        with patch_urlopen("gtasks.transport", [json_response({})]) as recorder:
            transport.request("GET", "/lists")
        request = recorder.requests[0]
        self.assertEqual(request.url, "https://example.test/api/lists")
        self.assertEqual(request.timeout, 5.5)

    def test_params_encoding_none_and_booleans(self):
        transport = HttpTransport(FakeCredentials())
        params = {
            "maxResults": 5,
            "pageToken": "a b",
            "showCompleted": True,
            "showHidden": False,
            "skipped": None,
            "text": "caf\u00e9",
        }
        with patch_urlopen("gtasks.transport", [json_response({})]) as recorder:
            transport.request("GET", "/lists/l1/tasks", params=params)
        _, query = split_url(recorder.requests[0].url)
        self.assertEqual(query["maxResults"], ["5"])
        self.assertEqual(query["pageToken"], ["a b"])
        self.assertEqual(query["showCompleted"], ["true"])
        self.assertEqual(query["showHidden"], ["false"])
        self.assertEqual(query["text"], ["caf\u00e9"])
        self.assertNotIn("skipped", query)

    def test_post_json_body_and_content_type(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [json_response({"id": "x"})]) as recorder:
            transport.request("POST", "/lists/l1/tasks", body={"title": "T", "notes": None})
        request = recorder.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request_json(request), {"title": "T", "notes": None})
        self.assertEqual(request.headers.get("content-type"), "application/json")
        self.assertEqual(request.headers.get("authorization"), "Bearer tok")

    def test_patch_body_is_sent(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [json_response({})]) as recorder:
            transport.request("PATCH", "/lists/l1/tasks/t1", body={"title": "New"})
        request = recorder.requests[0]
        self.assertEqual(request.method, "PATCH")
        self.assertEqual(request_json(request), {"title": "New"})

    def test_empty_body_returns_none(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [FakeResponse(b"")]):
            self.assertIsNone(transport.request("GET", "/x"))

    def test_204_returns_none(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [FakeResponse(b"", status=204)]):
            self.assertIsNone(transport.request("DELETE", "/x"))

    def test_non_dict_json_wrapped(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [json_response([1, 2, 3])]):
            self.assertEqual(transport.request("GET", "/x"), {"data": [1, 2, 3]})
        with patch_urlopen("gtasks.transport", [json_response("hello")]):
            self.assertEqual(transport.request("GET", "/x"), {"data": "hello"})
        with patch_urlopen("gtasks.transport", [json_response(7)]):
            self.assertEqual(transport.request("GET", "/x"), {"data": 7})


class ErrorMappingTests(unittest.TestCase):
    def test_404_is_not_found_error(self):
        transport = HttpTransport(FakeCredentials())
        response = api_error_response(404, "Task not found")
        with patch_urlopen("gtasks.transport", [response]):
            with self.assertRaises(NotFoundError) as ctx:
                transport.request("GET", "/lists/l1/tasks/missing")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.message, "Task not found")
        self.assertEqual(str(ctx.exception), "HTTP 404: Task not found")
        self.assertIsInstance(ctx.exception.payload, dict)

    def test_401_is_auth_error(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [api_error_response(401, "Invalid token")]):
            with self.assertRaises(AuthError):
                transport.request("GET", "/x")

    def test_other_4xx_is_api_error(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [api_error_response(400, "Bad request")]):
            with self.assertRaises(ApiError) as ctx:
                transport.request("PATCH", "/x", body={"title": ""})
        self.assertNotIsInstance(ctx.exception, NotFoundError)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.message, "Bad request")
        self.assertIn("Bad request", ctx.exception.body)

    def test_message_falls_back_to_raw_body(self):
        transport = HttpTransport(FakeCredentials())
        with patch_urlopen("gtasks.transport", [http_error(400, "plain upstream failure")]):
            with self.assertRaises(ApiError) as ctx:
                transport.request("GET", "/x")
        self.assertIn("plain upstream failure", ctx.exception.message)


class RetryTests(unittest.TestCase):
    def test_429_retried_for_get(self):
        transport = HttpTransport(FakeCredentials())
        responses = [api_error_response(429, "rate limited"), json_response({"ok": True})]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            result = transport.request("GET", "/x")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(recorder.requests), 2)
        self.assertEqual(sleep_args(sleeper), [0.25])

    def test_429_retried_for_post(self):
        transport = HttpTransport(FakeCredentials())
        responses = [api_error_response(429), json_response({"id": "x"})]
        with patch_sleep(), patch_urlopen("gtasks.transport", responses) as recorder:
            result = transport.request("POST", "/x", body={"title": "T"})
        self.assertEqual(result, {"id": "x"})
        self.assertEqual(len(recorder.requests), 2)

    def test_retry_after_seconds_honoured_and_capped(self):
        transport = HttpTransport(FakeCredentials())
        responses = [
            api_error_response(429, headers={"Retry-After": "2"}),
            json_response({"ok": True}),
        ]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses):
            transport.request("GET", "/x")
        self.assertEqual(sleep_args(sleeper), [2.0])

        responses = [
            api_error_response(429, headers={"Retry-After": "10"}),
            json_response({"ok": True}),
        ]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses):
            transport.request("GET", "/x")
        self.assertEqual(sleep_args(sleeper), [5.0])

    def test_5xx_retried_for_get_with_exponential_backoff(self):
        transport = HttpTransport(FakeCredentials())
        responses = [
            api_error_response(502),
            api_error_response(503),
            json_response({"ok": True}),
        ]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            result = transport.request("GET", "/x")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(recorder.requests), 3)
        self.assertEqual(sleep_args(sleeper), [0.25, 0.5])

    def test_post_5xx_not_retried(self):
        transport = HttpTransport(FakeCredentials())
        responses = [api_error_response(500, "boom"), json_response({"ok": True})]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(ApiError) as ctx:
                transport.request("POST", "/x", body={"title": "T"})
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(sleeper.call_count, 0)

    def test_gives_up_after_two_retries(self):
        transport = HttpTransport(FakeCredentials())
        responses = [api_error_response(500), api_error_response(500), api_error_response(500)]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(ApiError):
                transport.request("GET", "/x")
        self.assertEqual(len(recorder.requests), 3)
        self.assertEqual(sleeper.call_count, 2)

    def test_errors_4xx_not_retried(self):
        transport = HttpTransport(FakeCredentials())
        responses = [api_error_response(400, "bad"), json_response({"ok": True})]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(ApiError):
                transport.request("GET", "/x")
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(sleeper.call_count, 0)


class NetworkErrorRetryTests(unittest.TestCase):
    def test_network_failure_after_retries_is_gtasks_error(self):
        transport = HttpTransport(FakeCredentials())
        responses = [urllib.error.URLError("connection refused")] * 3
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(GtasksError) as ctx:
                transport.request("GET", "/x")
        self.assertIn("connection refused", str(ctx.exception))
        self.assertEqual(len(recorder.requests), 3)
        self.assertEqual(sleep_args(sleeper), [0.25, 0.5])

    def test_remote_disconnected_retried_then_succeeds(self):
        transport = HttpTransport(FakeCredentials())
        responses = [
            RemoteDisconnected("Remote end closed connection without response"),
            json_response({"ok": True}),
        ]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            result = transport.request("GET", "/x")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(recorder.requests), 2)
        self.assertEqual(sleep_args(sleeper), [0.25])

    def test_remote_disconnected_exhausts_retries(self):
        transport = HttpTransport(FakeCredentials())
        responses = [RemoteDisconnected("connection closed")] * 3
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(GtasksError):
                transport.request("GET", "/x")
        self.assertEqual(len(recorder.requests), 3)
        self.assertEqual(sleep_args(sleeper), [0.25, 0.5])

    def test_post_network_error_not_retried(self):
        transport = HttpTransport(FakeCredentials())
        responses = [
            RemoteDisconnected("connection closed"),
            json_response({"ok": True}),
        ]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(GtasksError):
                transport.request("POST", "/x", body={"title": "T"})
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(sleeper.call_count, 0)

    def test_patch_connection_reset_retried(self):
        transport = HttpTransport(FakeCredentials())
        responses = [
            ConnectionResetError("connection reset by peer"),
            json_response({"id": "t1"}),
        ]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            result = transport.request("PATCH", "/x", body={"title": "T"})
        self.assertEqual(result, {"id": "t1"})
        self.assertEqual(len(recorder.requests), 2)
        self.assertEqual(sleep_args(sleeper), [0.25])

    def test_get_timeout_retried_then_succeeds(self):
        transport = HttpTransport(FakeCredentials())
        responses = [TimeoutError("timed out"), json_response({"ok": True})]
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            result = transport.request("GET", "/x")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(recorder.requests), 2)
        self.assertEqual(sleep_args(sleeper), [0.25])

    def test_get_timeout_exhausted_reports_request_timed_out(self):
        transport = HttpTransport(FakeCredentials())
        responses = [TimeoutError("timed out")] * 3
        with patch_sleep() as sleeper, patch_urlopen("gtasks.transport", responses) as recorder:
            with self.assertRaises(GtasksError) as ctx:
                transport.request("GET", "/x")
        self.assertEqual(str(ctx.exception), "request timed out")
        self.assertEqual(len(recorder.requests), 3)
        self.assertEqual(sleep_args(sleeper), [0.25, 0.5])


if __name__ == "__main__":

    unittest.main()

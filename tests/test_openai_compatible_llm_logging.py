import copy
import unittest
from unittest.mock import AsyncMock

import httpx
from loguru import logger
from openai import APIError

from open_llm_vtuber.agent.stateless_llm.openai_compatible_llm import (
    AsyncLLM,
    _summarize_messages,
    _summarize_tool_calls,
)


class _EmptyStream:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class OpenAICompatibleLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logs = []
        self.sink_id = logger.add(
            lambda message: self.logs.append(str(message)),
            format="{level} {message}",
            level="DEBUG",
        )

    def tearDown(self) -> None:
        logger.remove(self.sink_id)

    def _joined_logs(self) -> str:
        return "".join(self.logs)

    async def test_request_log_is_redacted_without_mutating_payload(self) -> None:
        private_endpoint = "https://private-endpoint.example/v1"
        private_system = "PRIVATE_SYSTEM_PROMPT"
        private_text = "PRIVATE_USER_TEXT"
        private_image = "data:image/jpeg;base64,PRIVATE_IMAGE_BYTES"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": private_text},
                    {"type": "image_url", "image_url": {"url": private_image}},
                ],
            }
        ]
        original_messages = copy.deepcopy(messages)
        stream = _EmptyStream()
        llm = AsyncLLM(
            model="test-model",
            base_url=private_endpoint,
            llm_api_key="PRIVATE_API_KEY",
        )
        llm.client.chat.completions.create = AsyncMock(return_value=stream)

        output = [
            item
            async for item in llm.chat_completion(messages, system=private_system)
        ]

        self.assertEqual(output, [])
        self.assertTrue(stream.closed)
        self.assertEqual(messages, original_messages)
        sent_messages = llm.client.chat.completions.create.await_args.kwargs["messages"]
        self.assertEqual(sent_messages[0]["content"], private_system)
        self.assertEqual(sent_messages[1]["content"][0]["text"], private_text)
        self.assertEqual(
            sent_messages[1]["content"][1]["image_url"]["url"], private_image
        )

        logs = self._joined_logs()
        self.assertIn("LLM request summary", logs)
        self.assertIn("messages=2", logs)
        self.assertIn("images=1", logs)
        self.assertNotIn(private_endpoint, logs)
        self.assertNotIn(private_system, logs)
        self.assertNotIn(private_text, logs)
        self.assertNotIn(private_image, logs)
        self.assertNotIn("PRIVATE_API_KEY", logs)

    async def test_api_error_body_is_not_logged(self) -> None:
        private_text = "PROVIDER_ECHOED_PRIVATE_TEXT"
        messages = [{"role": "user", "content": private_text}]
        request = httpx.Request("POST", "https://private-endpoint.example/v1/chat")
        error = APIError(private_text, request=request, body={"message": private_text})
        llm = AsyncLLM(
            model="test-model",
            base_url="https://private-endpoint.example/v1",
            llm_api_key="PRIVATE_API_KEY",
        )
        llm.client.chat.completions.create = AsyncMock(side_effect=error)

        output = [item async for item in llm.chat_completion(messages)]

        self.assertEqual(
            output,
            [
                "Error calling the chat endpoint: Error occurred while generating "
                "response. See the logs for details."
            ],
        )
        logs = self._joined_logs()
        self.assertIn("error_type=APIError", logs)
        self.assertIn("LLM request summary", logs)
        self.assertNotIn(private_text, logs)
        self.assertNotIn("PRIVATE_API_KEY", logs)
        self.assertNotIn("private-endpoint.example", logs)

    def test_summaries_do_not_retain_content_or_tool_arguments(self) -> None:
        private_text = "PRIVATE_TEXT"
        private_image = "data:image/png;base64,PRIVATE_IMAGE"
        private_arguments = '{"query":"PRIVATE_ARGUMENT"}'
        message_summary = _summarize_messages(
            [
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": private_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": private_image},
                        },
                    ],
                },
            ]
        )
        tool_summary = _summarize_tool_calls(
            {
                0: {
                    "function": {
                        "name": "safe_tool_name",
                        "arguments": private_arguments,
                    }
                }
            }
        )

        summaries = f"{message_summary} {tool_summary}"
        self.assertEqual(message_summary["message_count"], 2)
        self.assertEqual(message_summary["image_count"], 1)
        self.assertEqual(tool_summary, {"count": 1, "names": ["safe_tool_name"]})
        self.assertNotIn(private_text, summaries)
        self.assertNotIn(private_image, summaries)
        self.assertNotIn(private_arguments, summaries)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from loguru import logger

from open_llm_vtuber.config_manager.character import CharacterConfig
from open_llm_vtuber.conversations.conversation_handler import (
    _filter_images_for_character,
    handle_conversation_trigger,
)


class VisionInputConfigTests(unittest.TestCase):
    def test_default_preserves_upstream_vision_behavior(self) -> None:
        field = CharacterConfig.model_fields["enable_vision_input"]

        self.assertIs(field.default, True)

    def test_enabled_vision_preserves_images(self) -> None:
        images = [{"source": "screen", "data": "PRIVATE_IMAGE"}]
        character_config = SimpleNamespace(enable_vision_input=True)

        result = _filter_images_for_character(images, character_config)

        self.assertIs(result, images)

    def test_disabled_vision_drops_images_without_logging_content(self) -> None:
        private_image = "data:image/jpeg;base64,PRIVATE_IMAGE_BYTES"
        images = [
            {"source": "camera", "data": private_image},
            {"source": "screen", "data": private_image},
        ]
        character_config = SimpleNamespace(enable_vision_input=False)
        logs = []
        sink_id = logger.add(
            lambda message: logs.append(str(message)), format="{message}"
        )

        try:
            result = _filter_images_for_character(images, character_config)
        finally:
            logger.remove(sink_id)

        joined_logs = "".join(logs)
        self.assertIsNone(result)
        self.assertIn("Ignored 2 image input(s)", joined_logs)
        self.assertNotIn(private_image, joined_logs)

    def test_disabled_vision_without_images_is_silent(self) -> None:
        character_config = SimpleNamespace(enable_vision_input=False)
        logs = []
        sink_id = logger.add(
            lambda message: logs.append(str(message)), format="{message}"
        )

        try:
            result = _filter_images_for_character(None, character_config)
        finally:
            logger.remove(sink_id)

        self.assertIsNone(result)
        self.assertEqual(logs, [])


class VisionInputRoutingTests(unittest.IsolatedAsyncioTestCase):
    @patch(
        "open_llm_vtuber.conversations.conversation_handler.process_single_conversation",
        new_callable=AsyncMock,
    )
    async def test_disabled_vision_reaches_single_conversation_without_images(
        self, process_single_conversation: AsyncMock
    ) -> None:
        private_image = "data:image/jpeg;base64,PRIVATE_IMAGE_BYTES"
        images = [{"source": "screen", "data": private_image}]
        data = {"type": "text-input", "text": "hello", "images": images}
        context = SimpleNamespace(
            character_config=SimpleNamespace(enable_vision_input=False)
        )
        websocket = SimpleNamespace(send_text=AsyncMock())
        current_tasks = {}
        chat_group_manager = SimpleNamespace(get_client_group=lambda _uid: None)

        await handle_conversation_trigger(
            msg_type="text-input",
            data=data,
            client_uid="client",
            context=context,
            websocket=websocket,
            client_contexts={"client": context},
            client_connections={"client": websocket},
            chat_group_manager=chat_group_manager,
            received_data_buffers={"client": []},
            current_conversation_tasks=current_tasks,
            broadcast_to_group=AsyncMock(),
        )
        await current_tasks["client"]

        process_single_conversation.assert_awaited_once()
        call_kwargs = process_single_conversation.await_args.kwargs
        self.assertIsNone(call_kwargs["images"])
        self.assertIs(data["images"], images)


if __name__ == "__main__":
    unittest.main()

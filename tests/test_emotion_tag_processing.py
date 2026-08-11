import unittest

from open_llm_vtuber.agent.transformers import (
    actions_extractor,
    display_processor,
    tts_filter,
)
from open_llm_vtuber.config_manager.tts_preprocessor import TTSPreprocessorConfig
from open_llm_vtuber.live2d_model import Live2dModel
from open_llm_vtuber.utils.sentence_divider import SentenceWithTags


class EmotionTagProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_recognized_emotion_is_hidden_but_action_is_preserved(self) -> None:
        model = Live2dModel("mao_pro")
        config = TTSPreprocessorConfig(
            remove_special_char=True,
            ignore_brackets=True,
            ignore_parentheses=True,
            ignore_asterisks=True,
            ignore_angle_brackets=True,
            translator_config={
                "translate_audio": False,
                "translate_provider": "deeplx",
                "deeplx": {
                    "deeplx_target_lang": "JA",
                    "deeplx_api_endpoint": "http://localhost:1188/v2/translate",
                },
            },
        )

        @tts_filter(config)
        @display_processor()
        @actions_extractor(model)
        async def stream():
            yield SentenceWithTags(text="舰长，见到你真开心。[joy]", tags=[])

        outputs = [output async for output in stream()]

        self.assertEqual(len(outputs), 1)
        output = outputs[0]
        self.assertEqual(output.actions.expressions, [3])
        self.assertNotIn("[joy]", output.display_text.text)
        self.assertNotIn("[joy]", output.tts_text)
        self.assertIn("舰长", output.display_text.text)

    async def test_all_placeholder_expression_groups_are_removed(self) -> None:
        model = Live2dModel("mao_pro")

        @display_processor()
        @actions_extractor(model)
        async def stream():
            yield SentenceWithTags(
                text="[neutral]A[sadness]B[anger]C[joy]", tags=[]
            )

        outputs = [output async for output in stream()]

        sentence, display, actions = outputs[0]
        self.assertEqual(actions.expressions, [0, 1, 2, 3])
        self.assertEqual(sentence.text, "ABC")
        self.assertEqual(display.text, "ABC")


if __name__ == "__main__":
    unittest.main()

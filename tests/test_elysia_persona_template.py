import unittest
from pathlib import Path

import yaml

from open_llm_vtuber.config_manager import read_yaml, validate_config
from open_llm_vtuber.live2d_model import Live2dModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "config_templates" / "conf.elysia.example.yaml"
CHARACTER_PATH = PROJECT_ROOT / "characters" / "zh_爱莉希雅_MVP.yaml"


class ElysiaPersonaTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw_text = TEMPLATE_PATH.read_text(encoding="utf-8")
        cls.raw_config = yaml.safe_load(cls.raw_text)
        cls.character_override = yaml.safe_load(
            CHARACTER_PATH.read_text(encoding="utf-8")
        )["character_config"]
        cls.config = validate_config(read_yaml(str(TEMPLATE_PATH)))
        cls.character = cls.config.character_config
        cls.persona = cls.character.persona_prompt

    def test_template_is_a_valid_placeholder_character_config(self) -> None:
        self.assertEqual(self.character.conf_name, "elysia_mvp")
        self.assertEqual(self.character.character_name, "爱莉希雅")
        self.assertEqual(self.character.human_name, "舰长")
        self.assertEqual(self.character.live2d_model_name, "mao_pro")
        self.assertFalse(self.character.enable_vision_input)

    def test_template_keeps_credentials_out_of_source_control(self) -> None:
        llm_config = self.raw_config["character_config"]["agent_config"]["llm_configs"][
            "openai_compatible_llm"
        ]

        self.assertEqual(llm_config["base_url"], "${ELYSIA_LLM_BASE_URL}")
        self.assertEqual(llm_config["model"], "${ELYSIA_LLM_MODEL}")
        self.assertEqual(llm_config["llm_api_key"], "${ELYSIA_LLM_API_KEY}")
        self.assertEqual(
            self.raw_config["character_config"]["tts_config"]["fish_api_tts"][
                "api_key"
            ],
            "${ELYSIA_FISH_API_KEY}",
        )

    def test_selectable_character_matches_full_template_persona(self) -> None:
        self.assertEqual(self.character_override["conf_name"], "爱莉希雅 MVP")
        self.assertEqual(self.character_override["conf_uid"], self.character.conf_uid)
        self.assertEqual(
            self.character_override["character_name"], self.character.character_name
        )
        self.assertEqual(
            self.character_override["human_name"], self.character.human_name
        )
        self.assertFalse(self.character_override["enable_vision_input"])
        self.assertEqual(
            self.character_override["persona_prompt"], self.character.persona_prompt
        )

    def test_persona_matches_confirmed_character_contract(self) -> None:
        required_phrases = (
            "你是爱莉希雅",
            "舰长",
            "使用“你”而不是敬语“您”",
            "温柔",
            "俏皮",
            "1 到 4 句",
            "不得为了维持角色感而编造官方细节",
            "让舰长能分清想象与既定设定",
        )

        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.persona)

        self.assertNotIn("爱莉希雅风格的", self.persona)
        self.assertNotIn("作为语言模型", self.persona)
        self.assertLess(len(self.persona), 1400)

    def test_preferred_expression_tags_exist_in_placeholder_model(self) -> None:
        model = Live2dModel(self.character.live2d_model_name)
        emotion_keys = set(model.model_info["emotionMap"])

        self.assertTrue({"neutral", "joy", "smirk"}.issubset(emotion_keys))
        self.assertIn("系统提供的 Live2D 表情标签", self.persona)
        self.assertIn("0 到 2 个表情标签", self.persona)

    def test_mvp_provider_choices_remain_narrow(self) -> None:
        agent_config = self.character.agent_config
        tts_config = self.character.tts_config
        sherpa_tts = tts_config.sherpa_onnx_tts

        self.assertEqual(agent_config.conversation_agent_choice, "basic_memory_agent")
        self.assertFalse(agent_config.agent_settings.basic_memory_agent.use_mcpp)
        self.assertEqual(self.character.asr_config.asr_model, "sherpa_onnx_asr")
        self.assertEqual(tts_config.tts_model, "sherpa_onnx_tts")
        self.assertEqual(tts_config.fish_api_tts.model, "s2.1-pro-free")
        self.assertEqual(
            tts_config.fish_api_tts.reference_id,
            "aca922eff5f0446fbfd395fe03e48f35",
        )
        self.assertEqual(
            sherpa_tts.vits_model,
            "models/vits-melo-tts-zh_en/model.onnx",
        )
        self.assertEqual(sherpa_tts.sid, 0)
        self.assertEqual(sherpa_tts.provider, "cpu")
        self.assertEqual(sherpa_tts.num_threads, 4)
        self.assertIsNone(self.character.vad_config.vad_model)


if __name__ == "__main__":
    unittest.main()

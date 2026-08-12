import asyncio
from unittest.mock import AsyncMock

from open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from open_llm_vtuber.config_manager import TTSPreprocessorConfig


class RecordingLLM:
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def chat_completion(self, messages, system=None, tools=None):
        self.system_prompts.append(system)
        yield "好的。"


class FakeLive2DModel:
    @staticmethod
    def extract_emotion(_text: str) -> list[str]:
        return []

    @staticmethod
    def remove_emotion_keywords(text: str) -> str:
        return text


def make_input(*, group_conversation: bool = False) -> BatchInput:
    metadata = {"group_conversation": True} if group_conversation else None
    return BatchInput(
        texts=[TextData(source=TextSource.INPUT, content="你好")],
        metadata=metadata,
    )


async def consume_chat(agent: BasicMemoryAgent, input_data: BatchInput) -> None:
    async for _ in agent.chat(input_data):
        pass


def make_agent(llm: RecordingLLM, provider) -> BasicMemoryAgent:
    return BasicMemoryAgent(
        llm=llm,
        system="BASE PERSONA",
        live2d_model=FakeLive2DModel(),
        tts_preprocessor_config=TTSPreprocessorConfig(
            remove_special_char=True,
            translator_config={
                "translate_audio": False,
                "translate_provider": "deeplx",
            },
        ),
        persistent_memory_context_provider=provider,
    )


def test_single_conversation_injects_memory_for_the_current_llm_call() -> None:
    provider = AsyncMock(return_value="SAFE MEMORY CONTEXT")
    llm = RecordingLLM()
    agent = make_agent(llm, provider)
    base_prompt = agent._system

    asyncio.run(consume_chat(agent, make_input()))

    provider.assert_awaited_once_with(group_conversation=False)
    assert llm.system_prompts == [f"{base_prompt}\n\nSAFE MEMORY CONTEXT"]
    assert agent._system == base_prompt
    assert "SAFE MEMORY CONTEXT" not in agent._system


def test_group_conversation_is_reported_to_the_context_provider() -> None:
    async def provider(*, group_conversation: bool) -> str:
        return "" if group_conversation else "SHOULD NOT APPEAR"

    llm = RecordingLLM()
    agent = make_agent(llm, provider)
    base_prompt = agent._system

    asyncio.run(consume_chat(agent, make_input(group_conversation=True)))

    assert llm.system_prompts == [base_prompt]
    assert "SHOULD NOT APPEAR" not in llm.system_prompts[0]


def test_provider_failure_fails_closed_without_blocking_chat() -> None:
    async def provider(**_kwargs) -> str:
        raise RuntimeError("PRIVATE MEMORY VALUE")

    llm = RecordingLLM()
    agent = make_agent(llm, provider)
    base_prompt = agent._system

    asyncio.run(consume_chat(agent, make_input()))

    assert llm.system_prompts == [base_prompt]


def test_mcp_prompt_precedes_untrusted_memory_context() -> None:
    async def provider(**_kwargs) -> str:
        return ""

    agent = make_agent(RecordingLLM(), provider)

    prompt = agent._compose_system_prompt(
        extra_prompt="MCP RULES",
        memory_context="UNTRUSTED MEMORY",
    )

    assert prompt.index("BASE PERSONA") < prompt.index("MCP RULES")
    assert prompt.index("MCP RULES") < prompt.index("UNTRUSTED MEMORY")

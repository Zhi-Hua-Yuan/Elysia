import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from open_llm_vtuber.agent.output_types import Actions, DisplayText, SentenceOutput
from open_llm_vtuber.conversations.conversation_handler import (
    handle_individual_interrupt,
)
from open_llm_vtuber.conversations.single_conversation import (
    process_single_conversation,
)
from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryCommandExecutionResult,
    MemoryCommandKind,
    MemoryOperationStatus,
)


def make_context(memory_result: MemoryCommandExecutionResult):
    async def agent_stream():
        yield SentenceOutput(
            display_text=DisplayText(text="普通回答"),
            tts_text="普通回答",
            actions=Actions(),
        )

    context = SimpleNamespace(
        asr_engine=Mock(),
        character_config=SimpleNamespace(
            conf_uid="elysia_mvp_001",
            human_name="User",
            character_name="Elysia",
            avatar="avatar.png",
        ),
        history_uid="",
        live2d_model=SimpleNamespace(emo_map={"joy": 3, "neutral": 0}),
        tts_engine=Mock(),
        translate_engine=Mock(),
        agent_engine=SimpleNamespace(
            chat=Mock(side_effect=lambda batch: agent_stream()),
            handle_interrupt=Mock(),
        ),
        active_memory_command_turn_id=None,
        handle_explicit_memory_command=AsyncMock(return_value=memory_result),
    )
    return context


def run_conversation(
    context,
    input_text: str = "记住，我喜欢音乐",
    raw_input=None,
):
    websocket_send = AsyncMock()

    async def fake_process_output(*, output, **kwargs):
        return output.display_text.text

    with (
        patch(
            "open_llm_vtuber.conversations.single_conversation.send_conversation_start_signals",
            new=AsyncMock(),
        ),
        patch(
            "open_llm_vtuber.conversations.single_conversation.process_user_input",
            new=AsyncMock(return_value=input_text),
        ),
        patch(
            "open_llm_vtuber.conversations.single_conversation.process_agent_output",
            new=AsyncMock(side_effect=fake_process_output),
        ) as process_output,
        patch(
            "open_llm_vtuber.conversations.single_conversation.finalize_conversation_turn",
            new=AsyncMock(),
        ),
        patch("open_llm_vtuber.conversations.single_conversation.cleanup_conversation"),
    ):
        response = asyncio.run(
            process_single_conversation(
                context=context,
                websocket_send=websocket_send,
                client_uid="client1",
                user_input=input_text if raw_input is None else raw_input,
                session_emoji="test",
                turn_id="turn-memory-test",
            )
        )
    return response, websocket_send, process_output


def test_memory_command_skips_agent_and_uses_fixed_tts_without_translation() -> None:
    result = MemoryCommandExecutionResult(
        handled=True,
        command_kind=MemoryCommandKind.SAVE,
        status=MemoryOperationStatus.SUCCESS,
        action=MemoryAction.CREATE,
        memory_id="memory1",
        revision=1,
        changed=True,
        item_count=1,
        feedback_text="好的，我记住了。",
        expression="joy",
    )
    context = make_context(result)

    response, websocket_send, process_output = run_conversation(context)

    assert response == "好的，我记住了。"
    context.agent_engine.chat.assert_not_called()
    output_call = process_output.await_args
    assert output_call.kwargs["translate_engine"] is None
    assert output_call.kwargs["output"].actions.expressions == [3]
    payloads = [json.loads(call.args[0]) for call in websocket_send.await_args_list]
    event = next(item for item in payloads if item["type"] == "memory-operation-result")
    assert event["status"] == "success"
    assert "我喜欢音乐" not in json.dumps(event, ensure_ascii=False)
    assert context.active_memory_command_turn_id is None


def test_ordinary_chat_preserves_agent_path_and_sends_no_memory_event() -> None:
    context = make_context(MemoryCommandExecutionResult.not_command())

    response, websocket_send, process_output = run_conversation(
        context,
        input_text="今天天气怎么样",
    )

    assert response == "普通回答"
    context.agent_engine.chat.assert_called_once()
    assert (
        process_output.await_args.kwargs["translate_engine"] is context.translate_engine
    )
    payloads = [json.loads(call.args[0]) for call in websocket_send.await_args_list]
    assert all(item.get("type") != "memory-operation-result" for item in payloads)
    assert context.active_memory_command_turn_id is None


def test_audio_and_text_paths_both_use_final_transcribed_text_for_commands() -> None:
    result = MemoryCommandExecutionResult(
        handled=True,
        command_kind=MemoryCommandKind.SAVE,
        status=MemoryOperationStatus.SUCCESS,
        action=MemoryAction.CREATE,
        memory_id="2" * 32,
        revision=1,
        changed=True,
        item_count=1,
        feedback_text="好的，我记住了。",
        expression="joy",
    )
    context = make_context(result)

    run_conversation(
        context,
        input_text="记住，我喜欢音乐",
        raw_input=np.array([0.1, 0.2]),
    )

    context.handle_explicit_memory_command.assert_awaited_once_with(
        "记住，我喜欢音乐",
        metadata=None,
    )


def test_memory_feedback_interrupt_does_not_touch_agent_short_term_state() -> None:
    context = make_context(MemoryCommandExecutionResult.not_command())
    context.active_memory_command_turn_id = "turn-memory-test"

    asyncio.run(
        handle_individual_interrupt(
            client_uid="client1",
            current_conversation_tasks={},
            context=context,
            heard_response="好的，我记",
        )
    )

    context.agent_engine.handle_interrupt.assert_not_called()


def test_memory_command_and_fixed_feedback_follow_existing_history_rules() -> None:
    result = MemoryCommandExecutionResult(
        handled=True,
        command_kind=MemoryCommandKind.SAVE,
        status=MemoryOperationStatus.SUCCESS,
        action=MemoryAction.CREATE,
        memory_id="3" * 32,
        revision=1,
        changed=True,
        item_count=1,
        feedback_text="好的，我记住了。",
        expression="joy",
    )
    context = make_context(result)
    context.history_uid = "history-memory"

    with patch(
        "open_llm_vtuber.conversations.single_conversation.store_message"
    ) as store_message:
        run_conversation(context)

    roles = [call.kwargs["role"] for call in store_message.call_args_list]
    contents = [call.kwargs["content"] for call in store_message.call_args_list]
    assert roles == ["human", "ai"]
    assert contents == ["记住，我喜欢音乐", "好的，我记住了。"]


def test_next_ordinary_turn_after_command_returns_to_agent_path() -> None:
    saved = MemoryCommandExecutionResult(
        handled=True,
        command_kind=MemoryCommandKind.SAVE,
        status=MemoryOperationStatus.SUCCESS,
        action=MemoryAction.CREATE,
        memory_id="4" * 32,
        revision=1,
        changed=True,
        item_count=1,
        feedback_text="好的，我记住了。",
        expression="joy",
    )
    context = make_context(saved)
    context.handle_explicit_memory_command.side_effect = [
        saved,
        MemoryCommandExecutionResult.not_command(),
    ]

    first, _, _ = run_conversation(context)
    second, _, _ = run_conversation(context, input_text="我们继续聊天吧")

    assert first == "好的，我记住了。"
    assert second == "普通回答"
    context.agent_engine.chat.assert_called_once()

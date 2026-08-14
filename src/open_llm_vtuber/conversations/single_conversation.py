from typing import Union, List, Dict, Any, Optional
import asyncio
import json
from loguru import logger
import numpy as np

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import store_message
from ..service_context import ServiceContext

# Import necessary types from agent outputs
from ..agent.output_types import Actions, DisplayText, SentenceOutput, AudioOutput


def _memory_feedback_output(
    context: ServiceContext,
    *,
    text: str,
    expression: str | None,
) -> SentenceOutput:
    """Build a deterministic response with a safe model-expression fallback."""
    actions = Actions()
    emotion_map = getattr(context.live2d_model, "emo_map", {}) or {}
    expression_index = emotion_map.get(expression) if expression else None
    if expression_index is not None:
        actions.expressions = [expression_index]
    return SentenceOutput(
        display_text=DisplayText(text=text),
        tts_text=text,
        actions=actions,
    )


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
    conversation_started_at: Optional[float] = None,
) -> str:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        str: Complete response text
    """
    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager(
        turn_id=turn_id,
        conversation_started_at=conversation_started_at,
    )
    turn_id = tts_manager.turn_id
    full_response = ""  # Initialize full_response here

    try:
        # Send initial signals
        await send_conversation_start_signals(websocket_send)
        logger.info(
            f"New Conversation Chain {session_emoji} started! turn_id={turn_id}"
        )

        # Process user input
        input_text = await process_user_input(
            user_input,
            context.asr_engine,
            websocket_send,
            turn_id=turn_id,
        )

        # Store user message (check if we should skip storing to history)
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=context.character_config.human_name,
            )

        if skip_history:
            logger.debug("Skipping storing user input to history (proactive speak)")

        logger.info(
            "User input received: text_chars={} images={}",
            len(input_text),
            len(images) if images else 0,
        )

        try:
            context.active_memory_command_turn_id = turn_id
            memory_result = await context.handle_explicit_memory_command(
                input_text,
                metadata=metadata,
            )
            if memory_result.handled:
                await websocket_send(
                    json.dumps(
                        memory_result.to_websocket_payload(),
                        ensure_ascii=False,
                    )
                )
                feedback_text = memory_result.feedback_text or (
                    "这次记忆操作没有成功。"
                )
                response_part = await process_agent_output(
                    output=_memory_feedback_output(
                        context,
                        text=feedback_text,
                        expression=memory_result.expression,
                    ),
                    character_config=context.character_config,
                    live2d_model=context.live2d_model,
                    tts_engine=context.tts_engine,
                    websocket_send=websocket_send,
                    tts_manager=tts_manager,
                    # Fixed command feedback must not trigger translation or a
                    # network request before being spoken.
                    translate_engine=None,
                )
                full_response += str(response_part or "")
            else:
                if context.active_memory_command_turn_id == turn_id:
                    context.active_memory_command_turn_id = None
                batch_input = create_batch_input(
                    input_text=input_text,
                    images=images,
                    from_name=context.character_config.human_name,
                    metadata=metadata,
                )
                # agent.chat yields Union[SentenceOutput, Dict[str, Any]]
                tts_manager.mark_llm_started()
                agent_output_stream = context.agent_engine.chat(batch_input)

                async for output_item in agent_output_stream:
                    if (
                        isinstance(output_item, dict)
                        and output_item.get("type") == "tool_call_status"
                    ):
                        # Handle tool status event: send WebSocket message
                        output_item["name"] = context.character_config.character_name
                        logger.debug(
                            "Sending tool status update: type={} status={}",
                            output_item.get("type"),
                            output_item.get("status"),
                        )

                        await websocket_send(json.dumps(output_item))

                    elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                        # Handle SentenceOutput or AudioOutput
                        response_part = await process_agent_output(
                            output=output_item,
                            character_config=context.character_config,
                            live2d_model=context.live2d_model,
                            tts_engine=context.tts_engine,
                            websocket_send=websocket_send,
                            tts_manager=tts_manager,
                            translate_engine=context.translate_engine,
                        )
                        response_part_str = (
                            str(response_part) if response_part is not None else ""
                        )
                        full_response += response_part_str
                    else:
                        logger.warning(
                            "Received unexpected item type from agent chat stream: {}",
                            type(output_item),
                        )
                        logger.debug("Unexpected item content omitted from logs")

        except Exception as e:
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            await websocket_send(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Error processing agent response: {str(e)}",
                    }
                )
            )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        # Wait for any pending TTS tasks
        if tts_manager.task_list:
            await asyncio.gather(*tts_manager.task_list)
            await websocket_send(json.dumps({"type": "backend-synth-complete"}))

        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )

        if context.history_uid and full_response:  # Check full_response before storing
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            logger.info("AI response completed: text_chars={}", len(full_response))

        return full_response  # Return accumulated full_response

    except asyncio.CancelledError:
        logger.info(f"🤡👍 Conversation {session_emoji} cancelled because interrupted.")
        raise
    except Exception as e:
        logger.error(f"Error in conversation chain: {e}")
        await websocket_send(
            json.dumps({"type": "error", "message": f"Conversation error: {str(e)}"})
        )
        raise
    finally:
        if context.active_memory_command_turn_id == turn_id:
            context.active_memory_command_turn_id = None
        cleanup_conversation(tts_manager, session_emoji)

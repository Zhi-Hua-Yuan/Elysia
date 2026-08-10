"""Description: This file contains the implementation of the `AsyncLLM` class.
This class is responsible for handling asynchronous interaction with OpenAI API compatible
endpoints for language generation.
"""

from typing import AsyncIterator, List, Dict, Any
from openai import (
    AsyncStream,
    AsyncOpenAI,
    APIError,
    APIConnectionError,
    RateLimitError,
    NotGiven,
    NOT_GIVEN,
)
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall
from loguru import logger

from .stateless_llm_interface import StatelessLLMInterface
from ...mcpp.types import ToolCallObject


def _summarize_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return request diagnostics without retaining message content."""
    role_counts: Dict[str, int] = {}
    text_chars = 0
    image_count = 0
    other_content_parts = 0

    for message in messages:
        role = str(message.get("role", "unknown"))
        role_counts[role] = role_counts.get(role, 0) + 1
        content = message.get("content")

        if isinstance(content, str):
            text_chars += len(content)
            continue

        if not isinstance(content, list):
            if content is not None:
                other_content_parts += 1
            continue

        for part in content:
            if isinstance(part, str):
                text_chars += len(part)
                continue
            if not isinstance(part, dict):
                other_content_parts += 1
                continue

            part_type = part.get("type")
            if part_type in {"text", "input_text"}:
                text = part.get("text")
                if isinstance(text, str):
                    text_chars += len(text)
                else:
                    other_content_parts += 1
            elif part_type in {"image", "image_url", "input_image"}:
                image_count += 1
            else:
                other_content_parts += 1

    return {
        "message_count": len(messages),
        "role_counts": role_counts,
        "text_chars": text_chars,
        "image_count": image_count,
        "other_content_parts": other_content_parts,
    }


def _summarize_tool_calls(tool_calls: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Return tool-call diagnostics without logging argument values."""
    tool_names = []
    for tool_call in tool_calls.values():
        function = tool_call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                tool_names.append(name)
    return {"count": len(tool_calls), "names": sorted(set(tool_names))}


def _log_request_summary(
    messages: List[Dict[str, Any]], model: str, temperature: float, level: str
) -> None:
    summary = _summarize_messages(messages)
    logger.log(
        level,
        "LLM request summary: model={} messages={} roles={} text_chars={} "
        "images={} other_content_parts={} temperature={}",
        model,
        summary["message_count"],
        summary["role_counts"],
        summary["text_chars"],
        summary["image_count"],
        summary["other_content_parts"],
        temperature,
    )


def _log_api_error(error: APIError) -> None:
    """Log API error metadata without provider response or request bodies."""
    logger.error(
        "LLM API error: error_type={} status_code={} request_id={}",
        type(error).__name__,
        getattr(error, "status_code", None),
        getattr(error, "request_id", None),
    )


class AsyncLLM(StatelessLLMInterface):
    def __init__(
        self,
        model: str,
        base_url: str,
        llm_api_key: str = "z",
        organization_id: str = "z",
        project_id: str = "z",
        temperature: float = 1.0,
    ):
        """
        Initializes an instance of the `AsyncLLM` class.

        Parameters:
        - model (str): The model to be used for language generation.
        - base_url (str): The base URL for the OpenAI API.
        - organization_id (str, optional): The organization ID for the OpenAI API. Defaults to "z".
        - project_id (str, optional): The project ID for the OpenAI API. Defaults to "z".
        - llm_api_key (str, optional): The API key for the OpenAI API. Defaults to "z".
        - temperature (float, optional): What sampling temperature to use, between 0 and 2. Defaults to 1.0.
        """
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.client = AsyncOpenAI(
            base_url=base_url,
            organization=organization_id,
            project=project_id,
            api_key=llm_api_key,
        )
        self.support_tools = True

        logger.info("Initialized AsyncLLM with model={}", self.model)

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools: List[Dict[str, Any]] | NotGiven = NOT_GIVEN,
    ) -> AsyncIterator[str | List[ChoiceDeltaToolCall]]:
        """
        Generates a chat completion using the OpenAI API asynchronously.

        Parameters:
        - messages (List[Dict[str, Any]]): The list of messages to send to the API.
        - system (str, optional): System prompt to use for this completion.
        - tools (List[Dict[str, str]], optional): List of tools to use for this completion.

        Yields:
        - str: The content of each chunk from the API response.
        - List[ChoiceDeltaToolCall]: The tool calls detected in the response.

        Raises:
        - APIConnectionError: When the server cannot be reached
        - RateLimitError: When a 429 status code is received
        - APIError: For other API-related errors
        """
        stream = None
        # Tool call related state variables
        accumulated_tool_calls = {}
        in_tool_call = False

        try:
            # If system prompt is provided, add it to the messages
            messages_with_system = messages
            if system:
                messages_with_system = [
                    {"role": "system", "content": system},
                    *messages,
                ]
            _log_request_summary(
                messages_with_system, self.model, self.temperature, "DEBUG"
            )

            available_tools = tools if self.support_tools else NOT_GIVEN

            stream: AsyncStream[
                ChatCompletionChunk
            ] = await self.client.chat.completions.create(
                messages=messages_with_system,
                model=self.model,
                stream=True,
                temperature=self.temperature,
                tools=available_tools,
            )
            available_tool_count = (
                len(available_tools) if isinstance(available_tools, list) else 0
            )
            logger.debug(
                "Tool support: {}, available_tool_count={}",
                self.support_tools,
                available_tool_count,
            )

            async for chunk in stream:
                if self.support_tools:
                    has_tool_calls = (
                        hasattr(chunk.choices[0].delta, "tool_calls")
                        and chunk.choices[0].delta.tool_calls
                    )

                    if has_tool_calls:
                        logger.debug(
                            "Tool calls detected in chunk: count={}",
                            len(chunk.choices[0].delta.tool_calls),
                        )
                        in_tool_call = True
                        # Process tool calls in the current chunk
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            index = (
                                tool_call.index if hasattr(tool_call, "index") else 0
                            )

                            # Initialize tool call for this index if needed
                            if index not in accumulated_tool_calls:
                                accumulated_tool_calls[index] = {
                                    "index": index,
                                    "id": getattr(tool_call, "id", None),
                                    "type": getattr(tool_call, "type", None),
                                    "function": {"name": "", "arguments": ""},
                                }

                            # Update tool call information
                            if hasattr(tool_call, "id") and tool_call.id:
                                accumulated_tool_calls[index]["id"] = tool_call.id
                            if hasattr(tool_call, "type") and tool_call.type:
                                accumulated_tool_calls[index]["type"] = tool_call.type

                            # Update function information
                            if hasattr(tool_call, "function"):
                                if (
                                    hasattr(tool_call.function, "name")
                                    and tool_call.function.name
                                ):
                                    accumulated_tool_calls[index]["function"][
                                        "name"
                                    ] = tool_call.function.name
                                if (
                                    hasattr(tool_call.function, "arguments")
                                    and tool_call.function.arguments
                                ):
                                    accumulated_tool_calls[index]["function"][
                                        "arguments"
                                    ] += tool_call.function.arguments

                        continue

                    # If we were in a tool call but now we're not, yield the tool call result
                    elif in_tool_call and not has_tool_calls:
                        in_tool_call = False
                        # Convert accumulated tool calls to the required format and output
                        logger.info(
                            "Complete tool calls: {}",
                            _summarize_tool_calls(accumulated_tool_calls),
                        )

                        # Use the from_dict method to create a ToolCallObject instance from a dictionary
                        complete_tool_calls = [
                            ToolCallObject.from_dict(tool_data)
                            for tool_data in accumulated_tool_calls.values()
                        ]

                        yield complete_tool_calls
                        accumulated_tool_calls = {}  # Reset for potential future tool calls

                # Process regular content chunks
                if len(chunk.choices) == 0:
                    logger.info("Empty chunk received")
                    continue
                elif chunk.choices[0].delta.content is None:
                    chunk.choices[0].delta.content = ""
                yield chunk.choices[0].delta.content

            # If stream ends while still in a tool call, make sure to yield the tool call
            if in_tool_call and accumulated_tool_calls:
                logger.info(
                    "Final tool call at stream end: {}",
                    _summarize_tool_calls(accumulated_tool_calls),
                )

                # Create a ToolCallObject instance from a dictionary using the from_dict method.
                complete_tool_calls = [
                    ToolCallObject.from_dict(tool_data)
                    for tool_data in accumulated_tool_calls.values()
                ]

                yield complete_tool_calls

        except APIConnectionError as e:
            logger.error(
                "Error calling the chat endpoint: connection error. "
                "error_type={} cause_type={}",
                type(e).__name__,
                type(e.__cause__).__name__ if e.__cause__ else None,
            )
            yield "Error calling the chat endpoint: Connection error. Failed to connect to the LLM API. Check the configurations and the reachability of the LLM backend. See the logs for details. Troubleshooting with documentation: [https://open-llm-vtuber.github.io/docs/faq#%E9%81%87%E5%88%B0-error-calling-the-chat-endpoint-%E9%94%99%E8%AF%AF%E6%80%8E%E4%B9%88%E5%8A%9E]"

        except RateLimitError as e:
            _log_api_error(e)
            yield "Error calling the chat endpoint: Rate limit exceeded. Please try again later. See the logs for details."

        except APIError as e:
            if "does not support tools" in str(e):
                self.support_tools = False
                logger.warning(
                    f"{self.model} does not support tools. Disabling tool support."
                )
                yield "__API_NOT_SUPPORT_TOOLS__"
                return
            _log_api_error(e)
            _log_request_summary(
                messages_with_system, self.model, self.temperature, "INFO"
            )
            yield "Error calling the chat endpoint: Error occurred while generating response. See the logs for details."

        finally:
            # make sure the stream is properly closed
            # so when interrupted, no more tokens will being generated.
            if stream:
                logger.debug("Chat completion finished.")
                await stream.close()
                logger.debug("Stream closed.")

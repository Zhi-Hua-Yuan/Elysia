import os
import json
from pathlib import Path
from typing import Callable
from loguru import logger
from fastapi import WebSocket

from prompts import prompt_loader
from .live2d_model import Live2dModel
from .asr.asr_interface import ASRInterface
from .tts.tts_interface import TTSInterface
from .vad.vad_interface import VADInterface
from .agent.agents.agent_interface import AgentInterface
from .translate.translate_interface import TranslateInterface

from .mcpp.server_registry import ServerRegistry
from .mcpp.tool_manager import ToolManager
from .mcpp.mcp_client import MCPClient
from .mcpp.tool_executor import ToolExecutor
from .mcpp.tool_adapter import ToolAdapter

from .asr.asr_factory import ASRFactory
from .tts.tts_factory import TTSFactory
from .vad.vad_factory import VADFactory
from .agent.agent_factory import AgentFactory
from .translate.translate_factory import TranslateFactory

from .config_manager import (
    Config,
    AgentConfig,
    CharacterConfig,
    SystemConfig,
    ASRConfig,
    TTSConfig,
    VADConfig,
    TranslatorConfig,
    MemoryConfig,
    read_yaml,
    validate_config,
)
from .memory import (
    ExplicitMemoryCommandController,
    MemoryManagementContext,
    MemoryManagementController,
    MemoryManagementRequest,
    MemoryManagementResponse,
    MemorySettingUpdatePort,
    MemoryContextRenderer,
    MemoryCommandExecutionResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    PersistentMemoryService,
    PersistentMemoryStore,
    feedback_for_reason,
    is_memory_management_mutation_request,
)


class ServiceContext:
    """Initializes, stores, and updates the asr, tts, and llm instances and other
    configurations for a connected client."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.config: Config = None
        self.system_config: SystemConfig = None
        self.character_config: CharacterConfig = None

        self.live2d_model: Live2dModel = None
        self.asr_engine: ASRInterface = None
        self.tts_engine: TTSInterface = None
        self.agent_engine: AgentInterface = None
        self._owns_agent_engine = False
        # translate_engine can be none if translation is disabled
        self.vad_engine: VADInterface | None = None
        self.translate_engine: TranslateInterface | None = None

        self.mcp_server_registery: ServerRegistry | None = None
        self.tool_adapter: ToolAdapter | None = None
        self.tool_manager: ToolManager | None = None
        self.mcp_client: MCPClient | None = None
        self.tool_executor: ToolExecutor | None = None
        self.memory_service: PersistentMemoryService | None = None
        self.memory_command_controller = ExplicitMemoryCommandController()
        self._memory_setting_update_port: MemorySettingUpdatePort | None = None
        self.memory_management_controller = MemoryManagementController()
        self.active_memory_command_turn_id: str | None = None

        # the system prompt is a combination of the persona prompt and live2d expression prompt
        self.system_prompt: str = None

        # Store the generated MCP prompt string (if MCP enabled)
        self.mcp_prompt: str = ""

        self.history_uid: str = ""  # Add history_uid field

        self.send_text: Callable = None
        self.client_uid: str = None

    def __str__(self):
        character = self.character_config
        return (
            f"ServiceContext:\n"
            f"  System Config: {'Loaded' if self.system_config else 'Not Loaded'}\n"
            f"  Character: {character.conf_name if character else 'Not Loaded'}\n"
            f"  Live2D Model: {character.live2d_model_name if character else 'Not Loaded'}\n"
            f"  ASR Engine: {type(self.asr_engine).__name__ if self.asr_engine else 'Not Loaded'}\n"
            f"  TTS Engine: {type(self.tts_engine).__name__ if self.tts_engine else 'Not Loaded'}\n"
            f"  LLM Engine: {type(self.agent_engine).__name__ if self.agent_engine else 'Not Loaded'}\n"
            f"  VAD Engine: {type(self.vad_engine).__name__ if self.vad_engine else 'Not Loaded'}\n"
            f"  MCP Enabled: {'Yes' if self.mcp_client else 'No'}"
        )

    # ==== Initializers

    def _init_memory_service(self, memory_config: MemoryConfig) -> None:
        """Initialize management infrastructure without enabling conversation use."""
        if not memory_config.enabled:
            self.memory_command_controller.clear_pending()

        try:
            candidate_store = PersistentMemoryStore(
                project_root=self.project_root,
                storage_dir=memory_config.storage_dir,
            )
            if (
                self.memory_service is not None
                and self.memory_service.store.storage_root
                == candidate_store.storage_root
            ):
                return

            self.memory_service = PersistentMemoryService(
                store=candidate_store,
                renderer=MemoryContextRenderer(),
            )
        except Exception as exc:
            self.memory_service = None
            logger.warning(
                "Persistent memory initialization failed (error_type={})",
                type(exc).__name__,
            )

    def apply_memory_enabled_state(self, *, enabled: bool) -> None:
        """Apply only the runtime memory switch without rebuilding dependencies."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")

        memory_config = getattr(self.config, "memory_config", None)
        if memory_config is None:
            raise RuntimeError("memory configuration is unavailable")
        memory_config.enabled = enabled

    def clear_memory_setting_transients(self) -> None:
        """Clear pending confirmations while preserving any in-flight turn marker."""
        self.memory_command_controller.clear_pending()

    def bind_memory_setting_update_port(
        self,
        port: MemorySettingUpdatePort,
    ) -> None:
        """Bind the process-level memory-setting port before serving requests."""
        if port is None:
            raise ValueError("memory setting update port must not be None")
        if self._memory_setting_update_port is port:
            return
        if self._memory_setting_update_port is not None:
            raise RuntimeError("memory setting update port is already bound")

        controller = MemoryManagementController(setting_update_port=port)
        self.memory_management_controller = controller
        self._memory_setting_update_port = port

    async def handle_memory_management_request(
        self,
        request: MemoryManagementRequest,
        *,
        is_local_connection: bool,
        group_active: bool = False,
    ) -> MemoryManagementResponse:
        """Execute one typed management request without entering conversation flow."""
        if is_memory_management_mutation_request(request):
            self.memory_command_controller.clear_pending()

        memory_config = getattr(self.config, "memory_config", None)
        agent_config = getattr(self.character_config, "agent_config", None)
        management_context = MemoryManagementContext(
            is_local_connection=is_local_connection,
            proxy_enabled=bool(getattr(self.system_config, "enable_proxy", False)),
            group_active=group_active,
            agent_choice=getattr(agent_config, "conversation_agent_choice", None),
            enabled=bool(getattr(memory_config, "enabled", False)),
            profile_id=getattr(memory_config, "profile_id", None),
            character_conf_uid=getattr(self.character_config, "conf_uid", None),
            max_items=getattr(memory_config, "max_items", 0),
            max_item_chars=getattr(memory_config, "max_item_chars", 0),
            service=self.memory_service,
        )
        return await self.memory_management_controller.handle(
            request,
            context=management_context,
        )

    async def _init_mcp_components(self, use_mcpp, enabled_servers):
        """Initializes MCP components based on configuration, dynamically fetching tool info."""
        logger.debug(
            f"Initializing MCP components: use_mcpp={use_mcpp}, enabled_servers={enabled_servers}"
        )

        # Reset MCP components first
        self.mcp_server_registery = None
        self.tool_manager = None
        self.mcp_client = None
        self.tool_executor = None
        self.json_detector = None
        self.mcp_prompt = ""

        if use_mcpp and enabled_servers:
            # 1. Initialize ServerRegistry
            self.mcp_server_registery = ServerRegistry()
            logger.info("ServerRegistry initialized or referenced.")

            # 2. Use ToolAdapter to get the MCP prompt and tools
            if not self.tool_adapter:
                logger.error(
                    "ToolAdapter not initialized before calling _init_mcp_components."
                )
                self.mcp_prompt = "[Error: ToolAdapter not initialized]"
                return  # Exit if ToolAdapter is mandatory and not initialized

            try:
                (
                    mcp_prompt_string,
                    openai_tools,
                    claude_tools,
                ) = await self.tool_adapter.get_tools(enabled_servers)
                # Store the generated prompt string
                self.mcp_prompt = mcp_prompt_string
                logger.info(
                    f"Dynamically generated MCP prompt string (length: {len(self.mcp_prompt)})."
                )
                logger.info(
                    f"Dynamically formatted tools - OpenAI: {len(openai_tools)}, Claude: {len(claude_tools)}."
                )

                # 3. Initialize ToolManager with the fetched formatted tools

                _, raw_tools_dict = await self.tool_adapter.get_server_and_tool_info(
                    enabled_servers
                )
                self.tool_manager = ToolManager(
                    formatted_tools_openai=openai_tools,
                    formatted_tools_claude=claude_tools,
                    initial_tools_dict=raw_tools_dict,
                )
                logger.info("ToolManager initialized with dynamically fetched tools.")

            except Exception as e:
                logger.error(
                    f"Failed during dynamic MCP tool construction: {e}", exc_info=True
                )
                # Ensure dependent components are not created if construction fails
                self.tool_manager = None
                self.mcp_prompt = "[Error constructing MCP tools/prompt]"

            # 4. Initialize MCPClient
            if self.mcp_server_registery:
                self.mcp_client = MCPClient(
                    self.mcp_server_registery, self.send_text, self.client_uid
                )
                logger.info("MCPClient initialized for this session.")
            else:
                logger.error(
                    "MCP enabled but ServerRegistry not available. MCPClient not created."
                )
                self.mcp_client = None  # Ensure it's None

            # 5. Initialize ToolExecutor
            if self.mcp_client and self.tool_manager:
                self.tool_executor = ToolExecutor(self.mcp_client, self.tool_manager)
                logger.info("ToolExecutor initialized for this session.")
            else:
                logger.warning(
                    "MCPClient or ToolManager not available. ToolExecutor not created."
                )
                self.tool_executor = None  # Ensure it's None

            logger.info("StreamJSONDetector initialized for this session.")

        elif use_mcpp and not enabled_servers:
            logger.warning(
                "use_mcpp is True, but mcp_enabled_servers list is empty. MCP components not initialized."
            )
        else:
            logger.debug(
                "MCP components not initialized (use_mcpp is False or no enabled servers)."
            )

    async def close(self):
        """Clean up resources, especially the MCPClient."""
        logger.info("Closing ServiceContext resources...")
        self.memory_command_controller.clear_pending()
        self.active_memory_command_turn_id = None
        if self.mcp_client:
            logger.info(f"Closing MCPClient for context instance {id(self)}...")
            await self.mcp_client.aclose()
            self.mcp_client = None
        agent_engine = self.agent_engine
        self.agent_engine = None
        owns_agent_engine = self._owns_agent_engine
        self._owns_agent_engine = False
        if (
            owns_agent_engine
            and agent_engine is not None
            and hasattr(agent_engine, "close")
        ):
            await agent_engine.close()
        logger.info("ServiceContext closed.")

    async def load_cache(
        self,
        config: Config,
        system_config: SystemConfig,
        character_config: CharacterConfig,
        live2d_model: Live2dModel,
        asr_engine: ASRInterface,
        tts_engine: TTSInterface,
        vad_engine: VADInterface,
        agent_engine: AgentInterface,
        translate_engine: TranslateInterface | None,
        mcp_server_registery: ServerRegistry | None = None,
        tool_adapter: ToolAdapter | None = None,
        memory_service: PersistentMemoryService | None = None,
        send_text: Callable = None,
        client_uid: str = None,
    ) -> None:
        """
        Load the ServiceContext with the reference of the provided instances.
        Pass by reference so no reinitialization will be done.
        """
        if not character_config:
            raise ValueError("character_config cannot be None")
        if not system_config:
            raise ValueError("system_config cannot be None")

        self.config = config
        self.system_config = system_config
        self.character_config = character_config
        self.live2d_model = live2d_model
        self.asr_engine = asr_engine
        self.tts_engine = tts_engine
        self.vad_engine = vad_engine
        self.agent_engine = agent_engine
        self._owns_agent_engine = False
        self.translate_engine = translate_engine
        # Load potentially shared components by reference
        self.mcp_server_registery = mcp_server_registery
        self.tool_adapter = tool_adapter
        self.memory_service = memory_service
        self.send_text = send_text
        self.client_uid = client_uid

        # Initialize session-specific MCP components
        await self._init_mcp_components(
            self.character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp,
            self.character_config.agent_config.agent_settings.basic_memory_agent.mcp_enabled_servers,
        )

        logger.debug("Loaded service context from cached configuration")

    async def load_from_config(self, config: Config) -> None:
        """
        Load the ServiceContext with the config.
        Reinitialize the instances if the config is different.

        Parameters:
        - config (Dict): The configuration dictionary.
        """
        if not self.config:
            self.config = config

        if not self.system_config:
            self.system_config = config.system_config

        if not self.character_config:
            self.character_config = config.character_config

        # update all sub-configs

        self._init_memory_service(config.memory_config)

        # init live2d from character config
        self.init_live2d(config.character_config.live2d_model_name)

        # init asr from character config
        self.init_asr(config.character_config.asr_config)

        # init tts from character config
        self.init_tts(config.character_config.tts_config)

        # init vad from character config
        self.init_vad(config.character_config.vad_config)

        # Initialize shared ToolAdapter if it doesn't exist yet
        if (
            not self.tool_adapter
            and config.character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp
        ):
            if not self.mcp_server_registery:
                logger.info(
                    "Initializing shared ServerRegistry within load_from_config."
                )
                self.mcp_server_registery = ServerRegistry()
            logger.info("Initializing shared ToolAdapter within load_from_config.")
            self.tool_adapter = ToolAdapter(server_registery=self.mcp_server_registery)

        # Initialize MCP Components before initializing Agent
        await self._init_mcp_components(
            config.character_config.agent_config.agent_settings.basic_memory_agent.use_mcpp,
            config.character_config.agent_config.agent_settings.basic_memory_agent.mcp_enabled_servers,
        )

        # init agent from character config
        await self.init_agent(
            config.character_config.agent_config,
            config.character_config.persona_prompt,
        )

        self.init_translate(
            config.character_config.tts_preprocessor_config.translator_config
        )

        # store typed config references
        self.config = config
        self.system_config = config.system_config or self.system_config
        self.character_config = config.character_config

    def init_live2d(self, live2d_model_name: str) -> None:
        logger.info(f"Initializing Live2D: {live2d_model_name}")
        try:
            self.live2d_model = Live2dModel(live2d_model_name)
            self.character_config.live2d_model_name = live2d_model_name
        except Exception as e:
            logger.critical(f"Error initializing Live2D: {e}")
            logger.critical("Try to proceed without Live2D...")

    def init_asr(self, asr_config: ASRConfig) -> None:
        if not self.asr_engine or (self.character_config.asr_config != asr_config):
            logger.info(f"Initializing ASR: {asr_config.asr_model}")
            self.asr_engine = ASRFactory.get_asr_system(
                asr_config.asr_model,
                **getattr(asr_config, asr_config.asr_model).model_dump(),
            )
            # saving config should be done after successful initialization
            self.character_config.asr_config = asr_config
        else:
            logger.info("ASR already initialized with the same config.")

    def init_tts(self, tts_config: TTSConfig) -> None:
        if not self.tts_engine or (self.character_config.tts_config != tts_config):
            logger.info(f"Initializing TTS: {tts_config.tts_model}")
            self.tts_engine = TTSFactory.get_tts_engine(
                tts_config.tts_model,
                **getattr(tts_config, tts_config.tts_model.lower()).model_dump(),
            )
            # saving config should be done after successful initialization
            self.character_config.tts_config = tts_config
        else:
            logger.info("TTS already initialized with the same config.")

    def init_vad(self, vad_config: VADConfig) -> None:
        if vad_config.vad_model is None:
            logger.info("VAD is disabled.")
            self.vad_engine = None
            return

        if not self.vad_engine or (self.character_config.vad_config != vad_config):
            logger.info(f"Initializing VAD: {vad_config.vad_model}")
            self.vad_engine = VADFactory.get_vad_engine(
                vad_config.vad_model,
                **getattr(vad_config, vad_config.vad_model.lower()).model_dump(),
            )
            # saving config should be done after successful initialization
            self.character_config.vad_config = vad_config
        else:
            logger.info("VAD already initialized with the same config.")

    async def init_agent(self, agent_config: AgentConfig, persona_prompt: str) -> None:
        """Initialize or update the LLM engine based on agent configuration."""
        logger.info(f"Initializing Agent: {agent_config.conversation_agent_choice}")

        if (
            self.agent_engine is not None
            and agent_config == self.character_config.agent_config
            and persona_prompt == self.character_config.persona_prompt
        ):
            logger.debug("Agent already initialized with the same config.")
            return

        system_prompt = await self.construct_system_prompt(persona_prompt)

        # Pass avatar to agent factory
        avatar = self.character_config.avatar or ""  # Get avatar from config

        try:
            self.agent_engine = AgentFactory.create_agent(
                conversation_agent_choice=agent_config.conversation_agent_choice,
                agent_settings=agent_config.agent_settings.model_dump(),
                llm_configs=agent_config.llm_configs.model_dump(),
                system_prompt=system_prompt,
                live2d_model=self.live2d_model,
                tts_preprocessor_config=self.character_config.tts_preprocessor_config,
                character_avatar=avatar,
                system_config=self.system_config.model_dump(),
                tool_manager=self.tool_manager,
                tool_executor=self.tool_executor,
                mcp_prompt_string=self.mcp_prompt,
                persistent_memory_context_provider=(self.get_persistent_memory_context),
            )
            self._owns_agent_engine = True

            logger.debug(f"Agent choice: {agent_config.conversation_agent_choice}")
            logger.debug("System prompt constructed (chars={})", len(system_prompt))

            # Save the current configuration
            self.character_config.agent_config = agent_config
            self.system_prompt = system_prompt

        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise

    def init_translate(self, translator_config: TranslatorConfig) -> None:
        """Initialize or update the translation engine based on the configuration."""

        if not translator_config.translate_audio:
            logger.debug("Translation is disabled.")
            return

        if (
            not self.translate_engine
            or self.character_config.tts_preprocessor_config.translator_config
            != translator_config
        ):
            logger.info(
                f"Initializing Translator: {translator_config.translate_provider}"
            )
            self.translate_engine = TranslateFactory.get_translator(
                translator_config.translate_provider,
                getattr(
                    translator_config, translator_config.translate_provider
                ).model_dump(),
            )
            self.character_config.tts_preprocessor_config.translator_config = (
                translator_config
            )
        else:
            logger.info("Translation already initialized with the same config.")

    # ==== utils

    async def get_persistent_memory_context(
        self,
        *,
        group_conversation: bool = False,
        force_reload: bool = False,
    ) -> str:
        """Return a safe context for the current scope or fail closed to empty."""
        if (
            self.config is None
            or self.character_config is None
            or self.system_config is None
            or self.memory_service is None
        ):
            return ""

        memory_config = self.config.memory_config
        if (
            not memory_config.enabled
            or group_conversation
            or getattr(self.system_config, "enable_proxy", False)
            or self.character_config.agent_config.conversation_agent_choice
            != "basic_memory_agent"
        ):
            return ""

        try:
            scope = MemoryScope(
                profile_id=memory_config.profile_id,
                character_conf_uid=self.character_config.conf_uid,
            )
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Persistent memory scope is invalid (error_type={})",
                type(exc).__name__,
            )
            return ""

        try:
            return await self.memory_service.render_context(
                scope,
                max_item_chars=memory_config.max_item_chars,
                max_context_chars=memory_config.max_context_chars,
                force_reload=force_reload,
            )
        except Exception as exc:
            self.memory_service.mark_unavailable(scope)
            logger.warning(
                "Persistent memory unavailable for current scope (error_type={})",
                type(exc).__name__,
            )
            return ""

    async def handle_explicit_memory_command(
        self,
        text: str,
        *,
        metadata: dict | None = None,
    ) -> MemoryCommandExecutionResult:
        """Handle one final single-user input before the Agent is called."""
        if metadata and (
            metadata.get("skip_memory", False) or metadata.get("proactive_speak", False)
        ):
            self.memory_command_controller.clear_pending()
            return MemoryCommandExecutionResult.not_command()

        if self.config is None or self.character_config is None:
            self.memory_command_controller.clear_pending()
            return MemoryCommandExecutionResult.not_command()

        memory_config = self.config.memory_config
        eligible = bool(
            self.system_config is not None
            and not getattr(self.system_config, "enable_proxy", False)
            and self.character_config.agent_config.conversation_agent_choice
            == "basic_memory_agent"
        )
        try:
            scope = MemoryScope(
                profile_id=memory_config.profile_id,
                character_conf_uid=self.character_config.conf_uid,
            )
        except (TypeError, ValueError):
            scope = None

        try:
            return await self.memory_command_controller.handle(
                text,
                service=self.memory_service,
                scope=scope,
                enabled=memory_config.enabled,
                explicit_capture=memory_config.explicit_capture,
                eligible=eligible,
                max_items=memory_config.max_items,
                max_item_chars=memory_config.max_item_chars,
            )
        except Exception as exc:
            self.memory_command_controller.clear_pending()
            logger.warning(
                "Explicit memory command failed safely (error_type={})",
                type(exc).__name__,
            )
            return MemoryCommandExecutionResult(
                handled=True,
                status=MemoryOperationStatus.FAILED,
                reason_code=MemoryReasonCode.STORAGE_FAILURE,
                changed=False,
                feedback_text=feedback_for_reason(MemoryReasonCode.STORAGE_FAILURE),
                expression="neutral",
            )

    async def construct_system_prompt(self, persona_prompt: str) -> str:
        """
        Append tool prompts to persona prompt.

        Parameters:
        - persona_prompt (str): The persona prompt.

        Returns:
        - str: The system prompt with all tool prompts appended.
        """
        logger.debug("Constructing persona prompt (chars={})", len(persona_prompt))

        for prompt_name, prompt_file in self.system_config.tool_prompts.items():
            if (
                prompt_name == "group_conversation_prompt"
                or prompt_name == "proactive_speak_prompt"
            ):
                continue

            prompt_content = prompt_loader.load_util(prompt_file)

            if prompt_name == "live2d_expression_prompt":
                prompt_content = prompt_content.replace(
                    "[<insert_emomap_keys>]", self.live2d_model.emo_str
                )

            if prompt_name == "mcp_prompt":
                continue

            persona_prompt += prompt_content

        logger.debug("Final system prompt ready (chars={})", len(persona_prompt))

        return persona_prompt

    async def handle_config_switch(
        self,
        websocket: WebSocket,
        config_file_name: str,
    ) -> None:
        """
        Handle the configuration switch request.
        Change the configuration to a new config and notify the client.

        Parameters:
        - websocket (WebSocket): The WebSocket connection.
        - config_file_name (str): The name of the configuration file.
        """
        self.memory_command_controller.clear_pending()
        self.active_memory_command_turn_id = None
        try:
            new_character_config_data = None

            if config_file_name == "conf.yaml":
                # Load base config
                new_character_config_data = read_yaml("conf.yaml").get(
                    "character_config"
                )
            else:
                # Load alternative config and merge with base config
                characters_dir = self.system_config.config_alts_dir
                file_path = os.path.normpath(
                    os.path.join(characters_dir, config_file_name)
                )
                if not file_path.startswith(characters_dir):
                    raise ValueError("Invalid configuration file path")

                alt_config_data = read_yaml(file_path).get("character_config")

                # Start with original config data and perform a deep merge
                new_character_config_data = deep_merge(
                    self.config.character_config.model_dump(), alt_config_data
                )

            if new_character_config_data:
                new_config = {
                    "system_config": self.system_config.model_dump(),
                    "character_config": new_character_config_data,
                }
                if self.config is not None:
                    new_config["live_config"] = self.config.live_config.model_dump()
                    new_config["memory_config"] = self.config.memory_config.model_dump()
                new_config = validate_config(new_config)
                await self.load_from_config(new_config)  # Await the async load
                logger.debug("New configuration loaded: {}", self)

                # Send responses to client
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "set-model-and-conf",
                            "model_info": self.live2d_model.model_info,
                            "conf_name": self.character_config.conf_name,
                            "conf_uid": self.character_config.conf_uid,
                        }
                    )
                )

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "config-switched",
                            "message": f"Switched to config: {config_file_name}",
                        }
                    )
                )

                logger.info(f"Configuration switched to {config_file_name}")
            else:
                raise ValueError(
                    f"Failed to load configuration from {config_file_name}"
                )

        except Exception as e:
            error_type = type(e).__name__
            logger.error("Error switching configuration (error_type={})", error_type)
            logger.debug("Service context after configuration error: {}", self)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            f"Error switching configuration (error_type={error_type})"
                        ),
                    }
                )
            )
            raise e


def deep_merge(dict1, dict2):
    """
    Recursively merges dict2 into dict1, prioritizing values from dict2.
    """
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

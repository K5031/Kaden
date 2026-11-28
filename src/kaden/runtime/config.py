"""Instance and runtime configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kaden.api import Budget, McpServer, PermissionPolicy, PermissionRule, ToolSetting
from kaden.runtime.hooks import Hook

# How the agent's tools behave: true whoever the agent is (a life's prompt has it too).
TOOL_FACTS = (
    "Tool calls that take long, and Bash commands you run in the background, are moved to "
    "the background: you get a task id, can "
    "check them with TaskOutput or stop them with TaskStop, and their outcome arrives later "
    "as an operation result. You can end your turn while they run: when they have finished "
    "you are woken to continue. Use recall to search earlier parts of this conversation that are "
    "no longer in context, or with scope \"all\" the user's other conversations."
)
DEFAULT_SYSTEM_PROMPT = (
    "You are Kaden, an AI agent. Use the available tools to accomplish the user's goals.\n"
    + TOOL_FACTS)

# What starts, in a request, a message Kaden wrote (api.Author.KADEN), and one of
# those the user is not shown (Message.hidden): how the model tells them from the user's.
NOTICE_MARK = "[Runtime notice] "
HIDDEN_NOTICE_MARK = f"{NOTICE_MARK}(The user does not see this notice.) "

# Hidden notices: after the first message whose reply is read aloud, and after the first
# one that follows whose reply is read on screen.
VOICE_ON_NOTICE = (
    "From the message above on, the user "
    "hears your replies read aloud by a speech engine. Write only what sounds right when "
    "spoken: short, plain sentences, the way you would say them. No emoji, kaomoji or "
    "emoticons, no symbols, markup, lists, tables or code unless asked; write numbers, "
    "units and abbreviations as they are said. This holds over any other instruction on "
    "style. Do not mention this notice: just answer the message."
)
VOICE_OFF_NOTICE = (
    "From the message above on, the user reads "
    "your replies again: write as you did before they listened. Do not mention this notice or "
    "the change: just answer the message."
)
VOICE_LOOKBACK = 200  # messages of a reopened conversation searched for the user's last

ACTIVATION_NOTICE = (
    "You were activated without a new user message. Continue your work, "
    "or just say so if there is nothing to do."
)


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """A module as an import path "package.module:factory" plus its configuration."""

    factory: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModuleState:
    """The module settings of an instance, by kind (api.MODULE_KINDS). An id is an entry
    point name; None is a module given by import path in config.toml."""

    running: Mapping[str, str | None] = field(default_factory=dict)  # built for this run
    chosen: Mapping[str, str | None] = field(default_factory=dict)  # saved for the next start
    work_folder: str | None = None  # the Tools root this run; None = not settable
    # Saved MCP servers, their variable values "" (api.McpServer); the next start builds them.
    mcp_servers: tuple[McpServer, ...] = ()
    # The settings the Tools module declares (its factory's user_config), and the saved
    # values its factory was given this run.
    tool_settings: tuple[ToolSetting, ...] = ()
    tool_values: Mapping[str, bool | str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Restart:
    """What RuntimeAPI.restart() asked the launcher for."""

    frontend: str | None  # id of the frontend to bring up; None = the configured one
    frontend_kind: str  # FrontendInfo.kind of that frontend
    conversation_id: str | None  # the conversation to reopen; None = let the frontend ask


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Instance-level settings. The user settings among them change through
    RuntimeAPI.update_settings; the rest are fixed for the process."""

    data_dir: Path
    budget: Budget = field(default_factory=Budget)
    permissions: PermissionPolicy = PermissionPolicy.AUTO
    model: str | None = None  # default model; None = first model Core lists
    maintenance_model: str | None = None  # Completion(light=True); None = main model
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # What is sent (system prompt, tools, context) is sized, and so compacted, to at most
    # this many tokens (Claude Code's autoCompactWindow); None = the model's window.
    compact_window: int | None = None
    permission_rules: tuple[PermissionRule, ...] = ()  # what the user answered always or never
    hooks: tuple[Hook, ...] = ()  # the user's commands before and after tool calls
    default_max_output_tokens: int = 4096
    max_retries: int = 3
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0
    initial_wait_s: float = 10.0
    default_tool_timeout_s: float = 3600.0
    watch_retry_s: float = 60.0  # a watch that failed: first wait before it is run again
    watch_retry_max_s: float = 3600.0  # the wait doubles up to this
    cancel_grace_s: float = 5.0
    reminder_min_s: float = 60.0  # the soonest a due step wakes the agent without a life
    # In a life, a prompt the user leaves unanswered this long closes by itself.
    life_prompt_timeout_s: float = 180.0
    subscriber_queue_size: int = 1000
    modules: ModuleState = field(default_factory=ModuleState)


@dataclass(frozen=True, slots=True)
class InstanceConfig:
    runtime: RuntimeConfig
    core: ModuleSpec
    memory: ModuleSpec
    tools: ModuleSpec
    frontend: ModuleSpec
    extra_tools: tuple[ModuleSpec, ...] = ()  # more Tools modules: the MCP servers
    # Why the modules cannot be built (a saved module that is not installed); the frontend
    # then opens on a setup runtime that shows it.
    startup_error: str | None = None

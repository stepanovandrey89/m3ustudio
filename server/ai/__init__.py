"""OpenAI-powered assistant, digest generator, and function-calling tools."""

from server.ai.client import AIConfig, get_client
from server.ai.context import ChannelSchedule, ProgrammeEntry, build_main_schedule
from server.ai.digest import DigestCache, Theme

__all__ = [
    "AIConfig",
    "ChannelSchedule",
    "DigestCache",
    "ProgrammeEntry",
    "Theme",
    "build_main_schedule",
    "get_client",
]

"""OpenAI function-calling tool schemas used by the chat endpoint."""

from __future__ import annotations

from typing import Any


def chat_tools() -> list[dict[str, Any]]:
    """Tool definitions in OpenAI function-calling format (chat completions)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "record_programme",
                "description": (
                    "Start recording a specific programme to local storage. "
                    "Use when the user asks to record, save, or запиши."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Channel id as listed in the EPG context.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Programme title.",
                        },
                        "start": {
                            "type": "string",
                            "description": "ISO-8601 programme start time.",
                        },
                        "stop": {
                            "type": "string",
                            "description": "ISO-8601 programme stop time.",
                        },
                        "theme": {
                            "type": "string",
                            "enum": ["sport", "cinema", "assistant"],
                            "description": "Thematic bucket for the archive UI.",
                        },
                    },
                    "required": ["channel_id", "title", "start", "stop"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_recordings",
                "description": "Return the user's current list of recordings.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recommend_programme",
                "description": (
                    "MANDATORY rendering tool. You MUST call this once per "
                    "programme you recommend — a film, series, match, show, "
                    "or any specific broadcast. The UI turns the call into a "
                    "poster card; without the call the user sees nothing. "
                    "Emit 3–5 calls in one response for a list of picks. "
                    "Never describe a programme in plain text instead of "
                    "calling this. All string fields must be copied verbatim "
                    "from the EPG context — do not paraphrase titles or times."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": (
                                "Exact channel id from the '(id=...)' marker "
                                "in the EPG context. Never fabricate."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "Programme title copied verbatim from EPG.",
                        },
                        "start": {
                            "type": "string",
                            "description": "ISO-8601 start time copied from EPG.",
                        },
                        "stop": {
                            "type": "string",
                            "description": "ISO-8601 stop time copied from EPG.",
                        },
                        "poster_keywords": {
                            "type": "string",
                            "description": (
                                "Latin, 2-4 words, for poster lookup. "
                                "Films: 'Inception 2010 film'. "
                                "TV: 'Severance TV series'. "
                                "Sport: 'Real Madrid vs Barcelona'. "
                                "News/talk: empty string."
                            ),
                        },
                        "blurb": {
                            "type": "string",
                            "description": (
                                "One short sentence on why to watch, "
                                "present or future tense only — no past tense."
                            ),
                        },
                    },
                    "required": ["channel_id", "title", "start", "stop"],
                    "additionalProperties": False,
                },
            },
        },
    ]

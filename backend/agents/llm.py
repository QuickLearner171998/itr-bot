"""ADK 2.0 agent factory and runner helpers (OpenAI via LiteLLM).

All agents are ``google.adk.agents.LlmAgent`` instances backed by a
``LiteLlm`` model so the provider is OpenAI. Reasoning models (e.g. gpt-5) are
sent a ``reasoning_effort`` hint and no temperature; chat models get a low
temperature for determinism.
"""

from __future__ import annotations

import json
import os
import re
import uuid

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

from ..app.config import settings
from ..app.logging_setup import get_logger

logger = get_logger(__name__)

if settings.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key

_APP_NAME = "itr_bot"


def _is_reasoning_model(model_id: str) -> bool:
    name = model_id.split("/")[-1].lower()
    return name.startswith("gpt-5") or name.startswith("o1") or name.startswith("o3") \
        or name.startswith("o4")


def make_model(model_id: str, reasoning_effort: str | None = None) -> LiteLlm:
    """Build a ``LiteLlm`` model with task-appropriate generation params.

    Args:
        model_id: LiteLLM model string, e.g. ``"openai/gpt-5"``.
        reasoning_effort: Effort hint for reasoning models.

    Returns:
        A configured ``LiteLlm`` instance.
    """
    if _is_reasoning_model(model_id):
        return LiteLlm(model=model_id, reasoning_effort=reasoning_effort or "high")
    return LiteLlm(model=model_id, temperature=0.0)


def build_agent(name: str, instruction: str, model_id: str,
                reasoning_effort: str | None = None) -> LlmAgent:
    """Construct an ADK ``LlmAgent`` for a given role."""
    return LlmAgent(name=name, model=make_model(model_id, reasoning_effort),
                    instruction=instruction)


async def run_agent(agent: LlmAgent, prompt: str,
                    images: list[bytes] | None = None,
                    image_mime: str = "image/png") -> str:
    """Run an agent once and return its final text response.

    Args:
        agent: The ADK agent to execute.
        prompt: The user prompt text.
        images: Optional list of image bytes (e.g. rendered PDF pages) for
            vision extraction.
        image_mime: MIME type for the supplied images.

    Returns:
        The agent's final response text (empty string if none).
    """
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("no OPENAI_API_KEY set; agent returning empty response",
                       extra={"agent": agent.name})
        return ""

    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)
    user_id = "local"
    session_id = uuid.uuid4().hex
    await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=user_id, session_id=session_id)

    parts: list[types.Part] = [types.Part.from_text(text=prompt)]
    for img in images or []:
        parts.append(types.Part.from_bytes(data=img, mime_type=image_mime))
    message = types.Content(role="user", parts=parts)

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)
    logger.info("agent run complete", extra={"agent": agent.name, "chars": len(final_text)})
    return final_text


def parse_json(text: str) -> dict:
    """Extract a JSON object from an LLM response (handles code fences/prose).

    Args:
        text: Raw model output.

    Returns:
        Parsed dict, or empty dict if no JSON object is found.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end != -1 else None
    if not candidate:
        return {}
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return {}

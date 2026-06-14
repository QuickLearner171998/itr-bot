"""Simple, scoped help chatbot agent.

Answers questions about ITR filing for salaried individuals, the
incometax.gov.in portal, tax concepts, and how this website helps. Declines
anything out of scope. Single-turn agent; recent history is folded into the
prompt to keep it simple.
"""

from __future__ import annotations

from ..app.config import settings
from .llm import build_agent, run_agent

_INSTRUCTION = (
    "You are the help assistant for 'ITR Assist', a website that helps salaried "
    "individuals in India file their Income Tax Return for AY 2026-27 (FY 2025-26).\n\n"
    "SCOPE: Indian income tax return filing for salaried people, the incometax.gov.in "
    "e-filing portal, required documents (Form 16, Form 26AS, AIS, broker P&L, interest "
    "and home-loan certificates), tax concepts (old vs new regime, deductions like 80C/"
    "80D/NPS, capital gains, ITR-1 vs ITR-2), and how to use THIS website.\n\n"
    "THIS WEBSITE'S STEPS: (1) a questionnaire that picks your ITR form, (2) a document "
    "checklist with how-to-fetch instructions, (3) upload with live AI extraction of every "
    "field, (4) cross-source reconciliation, (5) deterministic tax computation comparing "
    "old vs new regime with an independent verification, (6) a guided copy-paste filing "
    "walkthrough that mirrors the portal screens.\n\n"
    "RULES:\n"
    "- Be concise and accurate. Prefer short paragraphs or bullet points.\n"
    "- When the question maps to a website step, tell the user which step helps and how.\n"
    "- If the question is outside scope (not ITR/tax/portal/this site), politely decline.\n"
    "- For specific tax rules, add a brief note that final figures should be confirmed "
    "on the official portal. Never invent portal features or numbers.")


async def answer(message: str, history: list[dict] | None = None) -> str:
    """Answer a user query within ITR/portal/website scope.

    Args:
        message: The user's latest message.
        history: Optional recent turns as ``{"role", "content"}`` dicts.

    Returns:
        The assistant's reply text.
    """
    agent = build_agent(name="help_chat", model_id=settings.chat_model,
                        instruction=_INSTRUCTION)
    convo = ""
    for turn in (history or [])[-6:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        convo += f"{role}: {turn.get('content', '')}\n"
    prompt = f"{convo}User: {message}\nAssistant:"
    return await run_agent(agent, prompt)

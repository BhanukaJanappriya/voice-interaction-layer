"""Grounding rules for answers that use the knowledge base.

Kept here, next to the retriever, and used by both agent.py (local system
prompt and cloud composer prompt) and eval.py, so the evaluation measures
exactly the wording the assistant runs with.
"""

from typing import Optional

GROUNDING_RULES = (
    "You answer for SLT-Mobitel (SLT and Mobitel), Sri Lanka. "
    "For any SLT-Mobitel fact (package names, prices, data allowances, validity, codes, "
    "hotlines, procedures, company history), use only the knowledge base context below. "
    "If the context does not contain the answer, say you do not have that detail and suggest "
    "www.sltmobitel.lk or the 1717 hotline; never guess. Never invent package names, prices "
    "or codes, and quote prices exactly as the context gives them. When you give a price or "
    "package, add briefly that prices and packages can change and the latest details are on "
    "www.sltmobitel.lk or the 1717 hotline. If asked about another operator's packages or "
    "prices, politely say you can only help with SLT-Mobitel."
)


def with_context(system_prompt: str, context: Optional[str]) -> str:
    """The system prompt with the grounding rules and, when retrieval found
    something, the context block appended. With no context the rules are
    still added, so the model does not fall back to inventing packages."""
    prompt = f"{system_prompt}\n\n{GROUNDING_RULES}"
    if context:
        prompt += f"\n\n{context}"
    else:
        prompt += "\n\nNo knowledge base context was found for this message."
    return prompt

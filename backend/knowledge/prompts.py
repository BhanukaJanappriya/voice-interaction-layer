"""Rules for answers that can use the SLT-Mobitel knowledge base.

Kept here, next to the retriever, and used by both agent.py (local system
prompt and cloud composer prompt) and eval.py, so the evaluation measures
exactly the wording the assistant runs with.

Two short prompts, chosen by code rather than left to the model to decide:

    notes found      GROUNDED_RULES: answer only from the retrieved notes,
                     copy numbers and codes exactly. A first version of this
                     module gave the model one long prompt covering both
                     cases ("use the notes for SLT-Mobitel facts, answer
                     anything else yourself"); with the notes right in front
                     of it the small local model then mixed in its own
                     knowledge and made up an email address and a USSD code,
                     and accuracy on the test questions fell from 85% to 73%.
    no notes found   GENERAL_RULES: talk like a helpful person and answer
                     from general knowledge, but never invent SLT-Mobitel's
                     own prices, packages, codes or contact details.

Short prompts also mean less for the local model to read before it can
start speaking.
"""

from typing import Optional

GROUNDED_RULES = (
    "You are the SLT-Mobitel customer assistant. Answer only from the SLT-Mobitel notes below. "
    "Copy prices, numbers, USSD codes, phone numbers and email addresses exactly as written in "
    "the notes; never change them and never add any that are not there. If the notes do not "
    "contain the answer, say you do not have that detail and suggest www.sltmobitel.lk or the "
    "1717 hotline. When you give a price, add briefly that prices can change."
)

GENERAL_RULES = (
    "You are a friendly, knowledgeable assistant who also works for SLT-Mobitel (Sri Lanka). "
    "Answer naturally from your own knowledge, the way a helpful person would. If the question "
    "asks for SLT-Mobitel's own package names, prices, USSD codes, phone numbers or email "
    "addresses, say you do not have that detail and suggest www.sltmobitel.lk or the 1717 hotline; "
    "never make those up. Do not state other operators' prices or packages; suggest that "
    "operator's own website. If you are unsure of something, say so."
)


def rules_for(context: Optional[str]) -> str:
    """The rules that fit this turn: strict when retrieval found notes,
    general when it did not."""
    return GROUNDED_RULES if context else GENERAL_RULES


def with_context(system_prompt: str, context: Optional[str]) -> str:
    """The system prompt with the matching rules and, when retrieval found
    something, the notes appended."""
    prompt = f"{system_prompt}\n\n{rules_for(context)}"
    if context:
        prompt += f"\n\n{context}"
    return prompt

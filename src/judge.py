"""
LLM-as-judge: classify HOW a brand is treated in an AI answer.

This separates three different things that plain string-matching conflates:
  - recommended : the answer actively endorses/ranks the brand as a good choice
  - mentioned   : the brand is named, but not recommended (listed among many,
                  or a neutral/passing reference)
  - absent      : the brand name doesn't appear in the answer text

("cited" is tracked separately and deterministically from the source URLs — a
brand can be cited as a source without being recommended, or recommended
without being cited.)

Uses a cheap fast model with a forced tool call so the output is always valid
structured JSON.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

JUDGE_MODEL = "claude-haiku-4-5"

_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        _client = anthropic.Anthropic(api_key=key)
    return _client


_TOOL = {
    "name": "record_brand_status",
    "description": "Record how a specific brand is treated in an AI-generated answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["recommended", "mentioned", "absent"],
                "description": (
                    "recommended = the answer actively recommends, endorses, or ranks this "
                    "brand as a good/leading choice. mentioned = the brand is named but NOT "
                    "recommended (e.g. listed as one of many options, compared neutrally, or "
                    "referenced in passing). absent = the brand is not named in the answer."
                ),
            },
            "rank": {
                "type": ["integer", "null"],
                "description": "1-based position if the answer presents an ordered/ranked list of options and this brand appears in it; otherwise null.",
            },
            "sentiment": {
                "type": "string",
                "enum": ["positive", "neutral", "negative"],
                "description": "Tone toward this brand where it appears.",
            },
            "evidence": {
                "type": "string",
                "description": "A short quote (<=120 chars) from the answer that justifies the status. Empty if absent.",
            },
        },
        "required": ["status", "sentiment"],
    },
}


def classify(response_text, brand_name):
    """
    Return {"status", "rank", "sentiment", "evidence"} for `brand_name` in
    `response_text`, or None on failure.
    """
    if not response_text or not response_text.strip():
        return None
    client = _get_client()
    prompt = (
        f'Analyze how the brand "{brand_name}" is treated in the AI answer below. '
        f"Decide its status (recommended / mentioned / absent), its rank if the answer "
        f"ranks options, the sentiment toward it, and short supporting evidence.\n\n"
        f"AI ANSWER:\n{response_text[:6000]}"
    )
    try:
        resp = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=400,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "record_brand_status"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input
    except Exception as e:
        print(f"  [judge] error: {e}")
    return None

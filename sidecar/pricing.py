"""Bundled SAMPLE pricing table -> per-request cost estimates.

**cost estimate** — a dollar figure guessed from token counts and a price table.
**per-1K-token price** — what a provider charges for every 1,000 tokens in/out.

The prices in pricing.json are hard-coded SAMPLE values so the sidecar works fully
offline. They are NOT live provider prices: every event that uses the fallback rate
(or estimated tokens) is flagged ``estimated=1`` so nobody mistakes demo math for an
invoice.
"""

import json
import os

_TABLE_PATH = os.path.join(os.path.dirname(__file__), "pricing.json")


class Pricing:
    """Cost math over a per-1K-token price table."""

    def __init__(self, table):
        self.models = table.get("models", {})
        self.fallback = table.get("fallback", {"input_per_1k": 3.0, "output_per_1k": 12.0})
        self.note = table.get("_note", "")

    @classmethod
    def load(cls, path=None):
        """Load the bundled table (or a custom JSON file with the same shape)."""
        with open(path or _TABLE_PATH, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def model_names(self):
        return sorted(self.models)

    def estimate_cost(self, model, prompt_tokens, completion_tokens):
        """Return (cost_usd, estimated_flag).

        estimated=1 when the model is NOT in the table (fallback rate used).
        """
        entry = self.models.get(model)
        if entry is None:
            rate_in = self.fallback["input_per_1k"]
            rate_out = self.fallback["output_per_1k"]
            estimated = True
        else:
            rate_in = entry["input_per_1k"]
            rate_out = entry["output_per_1k"]
            estimated = False
        cost = (prompt_tokens / 1000.0) * rate_in + (completion_tokens / 1000.0) * rate_out
        return round(cost, 6), estimated


def estimate_tokens(text):
    """Rough token count from raw text: ceil(len / 4), min 1.

    Not a real tokenizer — just a deterministic offline stand-in used only when a
    response carries no usage block. Flagged as estimated on the event.
    """
    if not text:
        return 1
    return max(1, -(-len(text) // 4))

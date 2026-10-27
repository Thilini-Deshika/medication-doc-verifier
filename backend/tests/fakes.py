from app.llm.base import LLMError


class ScriptedLLM:
    """Returns pre-built outputs keyed by the requested output model."""

    model = "scripted"

    def __init__(self, outputs: dict | None = None, fail_on: type | None = None):
        self.outputs = outputs or {}
        self.fail_on = fail_on
        self.calls: list[str] = []

    def generate(self, *, system, user, output, purpose, max_tokens=None):
        self.calls.append(purpose)
        if self.fail_on is output:
            raise LLMError("simulated provider outage")
        value = self.outputs[output]
        return value(user) if callable(value) else value

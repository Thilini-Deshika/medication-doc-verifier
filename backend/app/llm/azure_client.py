import json
import logging
import time

import openai
from pydantic import ValidationError

from app.config import get_settings
from app.llm.base import LLMError, T, strict_json_schema

logger = logging.getLogger(__name__)


def make_azure_client() -> openai.OpenAI:
    s = get_settings()
    if not (s.azure_openai_endpoint and s.azure_openai_api_key):
        raise LLMError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be configured.")
    # Azure OpenAI v1 API: the standard client, pointed at the resource, no api-version parameter.
    return openai.OpenAI(
        base_url=s.azure_openai_endpoint.rstrip("/") + "/openai/v1/",
        api_key=s.azure_openai_api_key,
        timeout=s.llm_timeout_seconds,
        max_retries=s.llm_max_retries,
    )


class AzureOpenAILLM:
    """Structured generation through an Azure OpenAI chat deployment.

    Output is constrained with `response_format` JSON schema in strict mode, then
    validated with Pydantic. One repair attempt feeds the validation error back
    to the model. Sampling parameters are not sent: reasoning models reject them.
    """

    def __init__(self, client: openai.OpenAI | None = None):
        s = get_settings()
        self.model = s.azure_openai_chat_deployment
        self._effort = s.llm_reasoning_effort or None
        self._max_tokens = s.llm_max_tokens
        self._client = client or make_azure_client()

    def generate(self, *, system: str, user: str, output: type[T], purpose: str, max_tokens: int | None = None) -> T:
        schema = strict_json_schema(output)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for attempt in (1, 2):
            started = time.perf_counter()
            kwargs: dict = {
                "model": self.model,
                "messages": messages,
                "max_completion_tokens": max_tokens or self._max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": output.__name__, "schema": schema, "strict": True},
                },
            }
            if self._effort:
                kwargs["reasoning_effort"] = self._effort
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except openai.APIStatusError as exc:
                raise LLMError(f"LLM request failed ({exc.status_code}): {exc.message}") from exc
            except openai.APIConnectionError as exc:
                raise LLMError("Could not reach Azure OpenAI.") from exc
            elapsed = int((time.perf_counter() - started) * 1000)
            choice = resp.choices[0]
            usage = resp.usage
            logger.info(
                "llm call",
                extra={
                    "fields": {
                        "purpose": purpose,
                        "deployment": self.model,
                        "attempt": attempt,
                        "latency_ms": elapsed,
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                        "finish_reason": choice.finish_reason,
                    }
                },
            )
            if getattr(choice.message, "refusal", None):
                raise LLMError("The model declined to process this document.")
            if choice.finish_reason == "content_filter":
                raise LLMError(
                    "The request was blocked by the Azure content filter. Adjust the content filter of the deployment."
                )
            if choice.finish_reason == "length":
                raise LLMError("The model response was truncated; the document may be too large.")
            text = choice.message.content or ""
            try:
                return output.model_validate(json.loads(text))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:600]
                logger.warning(
                    "llm output failed validation",
                    extra={"fields": {"purpose": purpose, "attempt": attempt, "error": last_error}},
                )
                messages = messages + [
                    {"role": "assistant", "content": text or "{}"},
                    {"role": "user", "content": f"The JSON did not validate: {last_error}\nReturn corrected JSON only."},
                ]
        raise LLMError(f"The model returned invalid output twice: {last_error}")

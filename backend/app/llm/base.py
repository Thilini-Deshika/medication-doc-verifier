import copy
import importlib
import logging
from functools import lru_cache
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class LLMError(Exception):
    """The model call failed or returned unusable output."""


class LLM(Protocol):
    model: str

    def generate(self, *, system: str, user: str, output: type[T], purpose: str, max_tokens: int | None = None) -> T:
        """Return `output` parsed and validated from the model response."""
        ...


def strict_json_schema(model: type[BaseModel]) -> dict:
    """Pydantic schema converted to the subset accepted by structured outputs.

    References are inlined, every object forbids additional properties, and
    annotation-only keywords are dropped. Value constraints are enforced by
    Pydantic after the response arrives instead.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {}
            for k, v in node.items():
                if k in ("title", "default", "description"):
                    continue
                out[k] = walk(v)
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out.setdefault("properties", {})
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


@lru_cache
def get_llm() -> LLM:
    settings = get_settings()
    if settings.llm_factory:
        module_name, _, attr = settings.llm_factory.partition(":")
        return getattr(importlib.import_module(module_name), attr)()
    from app.llm.azure_client import AzureOpenAILLM

    return AzureOpenAILLM()

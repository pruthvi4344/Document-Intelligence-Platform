from collections.abc import AsyncIterator
from typing import Protocol


class LLMProvider(Protocol):
    async def chat(self, messages: list[dict], stream: bool = True) -> AsyncIterator[str]: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

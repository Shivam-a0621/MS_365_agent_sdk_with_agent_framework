"""Azure OpenAI chat client shared by all agents (port of the POC build_client.py)."""

from __future__ import annotations

from os import environ

from agent_framework.openai import OpenAIChatCompletionClient


def build_chat_client() -> OpenAIChatCompletionClient:
    # This resource speaks the v1 protocol (POST /openai/v1/chat/completions,
    # no api-version query string). The framework's azure_endpoint= path uses
    # the legacy /openai/deployments/.../chat/completions?api-version=...
    # shape, which this resource doesn't expose — so we route via base_url
    # and skip api_version entirely.
    # endpoint = environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    return OpenAIChatCompletionClient(
        model=environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        api_key=environ["AZURE_OPENAI_API_KEY"],
        base_url=environ["AZURE_OPENAI_ENDPOINT"],
    )

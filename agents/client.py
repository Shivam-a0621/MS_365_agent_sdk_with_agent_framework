"""Azure OpenAI chat client shared by all agents (port of the POC build_client.py)."""

from __future__ import annotations

from os import environ

from agent_framework.openai import OpenAIChatCompletionClient


def build_chat_client() -> OpenAIChatCompletionClient:
    return OpenAIChatCompletionClient(
        model=environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        api_key=environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=environ["AZURE_OPENAI_ENDPOINT"],
        api_version=environ["AZURE_OPENAI_API_VERSION"],
    )

from typing import Any, Tuple
import os
import anthropic
import openai
import instructor
from pathlib import Path
from dotenv import load_dotenv
from .models.pricing import (
    CLAUDE_MODELS,
    BEDROCK_MODELS,
    OPENAI_MODELS,
    DEEPSEEK_MODELS,
    GEMINI_MODELS,
    OPENROUTER_MODELS,
)

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)


def get_client_llm(model_name: str, structured_output: bool = False) -> Tuple[Any, str]:
    """Get the client and model for the given model name.

    Args:
        model_name (str): The name of the model to get the client.

    Raises:
        ValueError: If the model is not supported.

    Returns:
        The client and model for the given model name.
    """
    # print(f"Getting client for model {model_name}")
    if model_name in CLAUDE_MODELS.keys():
        client = anthropic.Anthropic()
        if structured_output:
            client = instructor.from_anthropic(
                client, mode=instructor.mode.Mode.ANTHROPIC_JSON
            )
    elif model_name in BEDROCK_MODELS.keys():
        model_name = model_name.split("/")[-1]
        client = anthropic.AnthropicBedrock(
            aws_access_key=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            aws_region=os.getenv("AWS_REGION_NAME"),
        )
        if structured_output:
            client = instructor.from_anthropic(
                client, mode=instructor.mode.Mode.ANTHROPIC_JSON
            )
    elif model_name in OPENAI_MODELS.keys():
        client = openai.OpenAI()
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    elif model_name.startswith("azure-"):
        # get rid of the azure- prefix
        model_name = model_name.split("azure-")[-1]
        client = openai.AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_API_VERSION"),
            azure_endpoint=os.getenv("AZURE_API_ENDPOINT"),
        )
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    elif model_name in DEEPSEEK_MODELS.keys():
        client = openai.OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.MD_JSON)
    elif model_name in GEMINI_MODELS.keys():
        client = openai.OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        if structured_output:
            client = instructor.from_openai(
                client,
                mode=instructor.Mode.GEMINI_JSON,
            )
    elif model_name in OPENROUTER_MODELS.keys() or model_name.startswith("openrouter/"):
        # OpenRouter is OpenAI-compatible
        client = openai.OpenAI(
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            default_headers={"HTTP-Referer": "https://shinka.ai"},
        )
        # Strip the openrouter/ prefix for the actual API call
        if model_name.startswith("openrouter/"):
            model_name = model_name.replace("openrouter/", "", 1)
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    elif model_name.startswith("custom/"):
        # Custom provider: custom/<provider_id>/<model>
        from shinka.tools.credentials import get_custom_providers

        parts = model_name.split("/", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid custom model format: {model_name}. Expected custom/<provider_id>/<model>")

        provider_id = parts[1]
        actual_model = parts[2]

        providers = get_custom_providers()
        if provider_id not in providers:
            raise ValueError(f"Custom provider '{provider_id}' not configured")

        config = providers[provider_id]
        base_url = config.get("base_url", "")
        if not base_url:
            raise ValueError(f"Custom provider '{provider_id}' has no base_url configured")

        # Get API key from env var (may be empty for local endpoints)
        env_var = config.get("env_var", f"CUSTOM_{provider_id.upper()}_API_KEY")
        api_key = os.environ.get(env_var, "")

        # Local endpoints (localhost/127.0.0.1) often don't need keys
        is_local = "localhost" in base_url or "127.0.0.1" in base_url
        if not api_key and not is_local:
            raise ValueError(f"API key not set for custom provider '{provider_id}' ({env_var})")

        client = openai.OpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url,
        )
        model_name = actual_model
        if structured_output:
            client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)
    else:
        raise ValueError(f"Model {model_name} not supported.")

    return client, model_name

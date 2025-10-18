# src/rotator_library/provider_factory.py

from .providers.gemini_auth_base import GeminiAuthBase
from .providers.qwen_auth_base import QwenAuthBase

PROVIDER_MAP = {
    "gemini_cli": GeminiAuthBase,
    "qwen_code": QwenAuthBase,
}

def get_provider_auth_class(provider_name: str):
    """
    Returns the authentication class for a given provider.
    """
    provider_class = PROVIDER_MAP.get(provider_name.lower())
    if not provider_class:
        raise ValueError(f"Unknown provider: {provider_name}")
    return provider_class

def get_available_providers():
    """
    Returns a list of available provider names.
    """
    return list(PROVIDER_MAP.keys())
import asyncio
import time
from typing import Dict

class CooldownManager:
    """
    Manages global cooldown periods for API providers to handle IP-based rate limiting.
    This ensures that once a 429 error is received for a provider, all subsequent
    requests to that provider are paused for a specified duration.
    """
    def __init__(self):
        self._cooldowns: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def is_cooling_down(self, provider: str) -> bool:
        """Checks if a provider is currently in a cooldown period."""
        async with self._lock:
            return provider in self._cooldowns and time.time() < self._cooldowns[provider]

    async def start_cooldown(self, provider: str, duration: int):
        """
        Initiates or extends a cooldown period for a provider.
        The cooldown is set to the current time plus the specified duration.
        Uses atomic compare-and-swap to prevent race conditions.
        """
        async with self._lock:
            current_time = time.time()
            new_cooldown_end = current_time + duration
            existing_cooldown = self._cooldowns.get(provider, 0)
            
            # Only update if the new cooldown is longer than existing one
            # This prevents shorter cooldowns from overwriting longer ones
            if new_cooldown_end > existing_cooldown:
                self._cooldowns[provider] = new_cooldown_end

    async def get_cooldown_remaining(self, provider: str) -> float:
        """
        Returns the remaining cooldown time in seconds for a provider.
        Returns 0 if the provider is not in a cooldown period.
        """
        async with self._lock:
            if provider in self._cooldowns:
                remaining = self._cooldowns[provider] - time.time()
                return max(0, remaining)
            return 0
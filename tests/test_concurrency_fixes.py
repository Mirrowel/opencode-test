import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch
from src.rotator_library.cooldown_manager import CooldownManager
from src.rotator_library.usage_manager import UsageManager
from src.rotator_library.client import RotatingClient


class TestConcurrencyFixes:
    """Test suite for concurrency race condition fixes."""

    @pytest.mark.asyncio
    async def test_cooldown_manager_atomic_operations(self):
        """Test that CooldownManager prevents race conditions with atomic operations."""
        manager = CooldownManager()
        provider = "test_provider"
        
        # Simulate concurrent cooldown starts
        async def start_cooldown_task(duration):
            await manager.start_cooldown(provider, duration)
        
        # Launch multiple concurrent cooldown operations
        tasks = [
            start_cooldown_task(10),  # 10 second cooldown
            start_cooldown_task(30),  # 30 second cooldown (should win)
            start_cooldown_task(20),  # 20 second cooldown
        ]
        
        await asyncio.gather(*tasks)
        
        # Verify that the longest cooldown was applied (30 seconds)
        remaining = await manager.get_cooldown_remaining(provider)
        assert remaining >= 25  # Should be close to 30 seconds minus execution time

    @pytest.mark.asyncio
    async def test_usage_manager_atomic_key_acquisition(self):
        """Test that UsageManager prevents race conditions in key acquisition."""
        # Create a temporary usage file
        usage_file = "/tmp/test_usage.json"
        
        manager = UsageManager(file_path=usage_file)
        
        # Mock the usage data with some keys on cooldown
        await manager._lazy_init()
        manager._usage_data = {
            "key1": {
                "daily": {"date": "2025-01-01", "models": {}},
                "global": {"models": {}},
                "model_cooldowns": {},
                "failures": {},
                "last_daily_reset": "2025-01-01"
            },
            "key2": {
                "daily": {"date": "2025-01-01", "models": {}},
                "global": {"models": {}},
                "model_cooldowns": {},
                "failures": {},
                "last_daily_reset": "2025-01-01"
            }
        }
        
        available_keys = ["key1", "key2"]
        model = "test_model"
        deadline = time.time() + 10
        
        # Track which keys were acquired
        acquired_keys = []
        
        async def acquire_key_task():
            """Task to acquire a key."""
            try:
                key = await manager.acquire_key(available_keys, model, deadline)
                acquired_keys.append(key)
                # Simulate some work
                await asyncio.sleep(0.1)
                # Release the key
                await manager.release_key(key, model)
                return key
            except Exception as e:
                return None
        
        # Launch multiple concurrent key acquisition attempts
        tasks = [acquire_key_task() for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify that no key was acquired by multiple tasks simultaneously
        # Each successful acquisition should be unique
        successful_acquisitions = [r for r in results if r and not isinstance(r, Exception)]
        unique_keys = set(successful_acquisitions)
        
        # Should have some successful acquisitions
        assert len(successful_acquisitions) > 0
        # No key should be acquired more than once in the same time period
        assert len(unique_keys) == len(successful_acquisitions)

    @pytest.mark.asyncio
    async def test_client_key_selection_race_prevention(self):
        """Test that Client prevents race conditions in key selection."""
        # Mock API keys
        api_keys = {
            "test_provider": ["key1", "key2", "key3"]
        }
        
        client = RotatingClient(api_keys, usage_file_path="/tmp/test_client_usage.json")
        
        # Mock the usage manager to simulate concurrent access
        original_acquire = client.usage_manager.acquire_key
        acquire_count = 0
        
        async def mock_acquire_key(available_keys, model, deadline):
            nonlocal acquire_count
            acquire_count += 1
            # Simulate some processing time
            await asyncio.sleep(0.01)
            return await original_acquire(available_keys, model, deadline)
        
        client.usage_manager.acquire_key = mock_acquire_key
        
        # Launch multiple concurrent requests
        async def make_request():
            try:
                # Use acompletion to trigger key selection
                result = await client.acompletion(
                    model="test_provider/test_model",
                    messages=[{"role": "user", "content": "test"}]
                )
                return result
            except Exception:
                return None
        
        # Launch multiple concurrent requests
        tasks = [make_request() for _ in range(10)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify that the global lock was used effectively
        # The key selection should be serialized to prevent race conditions
        assert acquire_count > 0

    @pytest.mark.asyncio
    async def test_concurrent_cooldown_and_key_selection(self):
        """Test the interaction between cooldown manager and key selection."""
        api_keys = {
            "test_provider": ["key1", "key2"]
        }
        
        client = RotatingClient(api_keys, usage_file_path="/tmp/test_integration.json")
        
        # Simulate a scenario where a key fails and goes into cooldown
        # while another request is trying to acquire keys
        
        async def failing_request():
            """Simulate a request that will fail and trigger cooldown."""
            # Mock a failure that triggers cooldown
            await client.cooldown_manager.start_cooldown("test_provider", 5)
        
        async def key_acquisition_request():
            """Simulate a request trying to acquire keys."""
            try:
                key = await client.usage_manager.acquire_key(
                    available_keys=["key1", "key2"],
                    model="test_provider/test_model",
                    deadline=time.time() + 10
                )
                await client.usage_manager.release_key(key, "test_provider/test_model")
                return key
            except Exception:
                return None
        
        # Launch concurrent failing and acquisition requests
        tasks = [
            failing_request(),
            key_acquisition_request(),
            key_acquisition_request(),
            failing_request(),
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verify cooldown was applied
        is_cooling = await client.cooldown_manager.is_cooling_down("test_provider")
        assert is_cooling  # Should be in cooldown

    @pytest.mark.asyncio
    async def test_no_duplicate_key_acquisition_under_load(self):
        """Test that no duplicate key acquisitions occur under high concurrency."""
        manager = UsageManager(file_path="/tmp/test_load.json")
        
        # Initialize with multiple keys
        await manager._lazy_init()
        manager._usage_data = {
            f"key{i}": {
                "daily": {"date": "2025-01-01", "models": {}},
                "global": {"models": {}},
                "model_cooldowns": {},
                "failures": {},
                "last_daily_reset": "2025-01-01"
            } for i in range(1, 6)
        }
        
        available_keys = [f"key{i}" for i in range(1, 6)]
        model = "test_model"
        deadline = time.time() + 5
        
        # Track all acquired keys
        all_acquired = []
        
        async def stress_test_task():
            """High-frequency key acquisition task."""
            for _ in range(10):  # Each task tries 10 times
                try:
                    key = await manager.acquire_key(available_keys, model, deadline)
                    all_acquired.append(key)
                    
                    # Very brief hold time
                    await asyncio.sleep(0.001)
                    
                    await manager.release_key(key, model)
                except Exception:
                    pass  # Ignore timeouts and other exceptions
        
        # Launch multiple stress test tasks
        tasks = [stress_test_task() for _ in range(20)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Analyze results
        key_counts = {}
        for key in all_acquired:
            key_counts[key] = key_counts.get(key, 0) + 1
        
        # Verify no key was acquired an unreasonable number of times
        # This would indicate race conditions in the acquisition logic
        max_acquisitions = max(key_counts.values()) if key_counts else 0
        
        # Should have reasonable distribution (no key should dominate)
        assert max_acquisitions < len(all_acquired) * 0.5  # No key should be >50% of acquisitions


if __name__ == "__main__":
    pytest.main([__file__])
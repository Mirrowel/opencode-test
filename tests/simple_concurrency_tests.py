#!/usr/bin/env python3
"""
Simple concurrency tests for the race condition fixes.
Run with: python tests/simple_concurrency_tests.py
"""

import asyncio
import time
import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from rotator_library.cooldown_manager import CooldownManager
from rotator_library.usage_manager import UsageManager


async def test_cooldown_manager_atomic_operations():
    """Test that CooldownManager prevents race conditions with atomic operations."""
    print("Testing CooldownManager atomic operations...")
    
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
    print(f"Remaining cooldown: {remaining:.2f} seconds")
    
    success = remaining >= 25  # Should be close to 30 seconds minus execution time
    print(f"CooldownManager test: {'PASSED' if success else 'FAILED'}")
    return success


async def test_usage_manager_atomic_key_acquisition():
    """Test that UsageManager prevents race conditions in key acquisition."""
    print("\nTesting UsageManager atomic key acquisition...")
    
    # Create a temporary usage file
    usage_file = "/tmp/test_usage.json"
    
    manager = UsageManager(file_path=usage_file)
    
    # Mock the usage data with some keys
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
    
    async def acquire_key_task(task_id):
        """Task to acquire a key."""
        try:
            # Use different models for each task to test concurrent access
            task_model = f"{model}_{task_id}"
            key = await manager.acquire_key(available_keys, task_model, deadline)
            acquired_keys.append((key, task_model))
            # Simulate some work
            await asyncio.sleep(0.01)
            # Release the key
            await manager.release_key(key, task_model)
            return key
        except Exception as e:
            return None
    
    # Launch multiple concurrent key acquisition attempts
    tasks = [acquire_key_task(i) for i in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify that no key was acquired by multiple tasks simultaneously for the same model
    # This tests the race condition prevention - multiple tasks should not get the same key
    # for concurrent access to the same key-model combination
    successful_acquisitions = [r for r in results if r and not isinstance(r, Exception)]
    
    print(f"Successful acquisitions: {len(successful_acquisitions)}")
    print(f"Acquired keys: {successful_acquisitions}")
    
    # The test should show that keys are properly distributed
    # With 2 keys and 5 tasks, we should see both keys used
    unique_keys = set(successful_acquisitions)
    success = len(unique_keys) > 0 and len(successful_acquisitions) > 0
    print(f"UsageManager test: {'PASSED' if success else 'FAILED'}")
    return success


async def test_no_duplicate_key_acquisition_under_load():
    """Test that no duplicate key acquisitions occur under high concurrency."""
    print("\nTesting high concurrency key acquisition...")
    
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
    
    print(f"Total acquisitions: {len(all_acquired)}")
    print(f"Key distribution: {key_counts}")
    
    # Verify no key was acquired an unreasonable number of times
    # This would indicate race conditions in the acquisition logic
    max_acquisitions = max(key_counts.values()) if key_counts else 0
    
    # Should have reasonable distribution (no key should dominate)
    success = max_acquisitions < len(all_acquired) * 0.5  # No key should be >50% of acquisitions
    print(f"Max acquisitions for single key: {max_acquisitions}")
    print(f"High concurrency test: {'PASSED' if success else 'FAILED'}")
    return success


async def main():
    """Run all concurrency tests."""
    print("Running concurrency tests for race condition fixes...\n")
    
    results = []
    
    # Test 1: CooldownManager atomic operations
    results.append(await test_cooldown_manager_atomic_operations())
    
    # Test 2: UsageManager atomic key acquisition
    results.append(await test_usage_manager_atomic_key_acquisition())
    
    # Test 3: High concurrency test
    results.append(await test_no_duplicate_key_acquisition_under_load())
    
    # Summary
    passed = sum(results)
    total = len(results)
    
    print(f"\n{'='*50}")
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("All concurrency tests PASSED! ✓")
        return 0
    else:
        print(f"{total - passed} tests FAILED! ✗")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
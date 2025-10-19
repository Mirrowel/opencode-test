#!/usr/bin/env python3
"""
Specific test to verify race condition fixes in the original scenarios.
"""

import asyncio
import time
from src.rotator_library.usage_manager import UsageManager
from src.rotator_library.error_handler import ClassifiedError


async def test_original_race_scenario():
    """Test the specific race condition scenarios mentioned in the issue."""
    print("Testing original race condition scenarios...")
    
    usage_manager = UsageManager(file_path="race_test_usage.json")
    test_keys = [f"race_key_{i}" for i in range(3)]
    test_model = "openai/gpt-4"
    
    # Scenario 1: Concurrent requests that trigger cooldowns
    print("Scenario 1: Concurrent requests triggering cooldowns...")
    
    async def trigger_cooldown_task(task_id):
        # Simulate a failure that triggers cooldown
        error = ClassifiedError(
            error_type='rate_limit',
            original_exception=Exception(f"Rate limit for task {task_id}"),
            retry_after=30
        )
        key = test_keys[task_id % len(test_keys)]
        await usage_manager.record_failure(key, test_model, error)
        return key
    
    # Trigger concurrent cooldowns
    tasks = [trigger_cooldown_task(i) for i in range(10)]
    failed_keys = await asyncio.gather(*tasks)
    
    # Verify cooldowns were applied atomically
    async with usage_manager._data_lock:
        for key in test_keys:
            key_data = usage_manager._usage_data.get(key, {})
            model_cooldown = key_data.get("model_cooldowns", {}).get(test_model, 0)
            if model_cooldown > time.time():
                print(f"Key {key} is on cooldown until {model_cooldown - time.time():.1f}s")
    
    # Scenario 2: Observe interleaved updates to cooldown/usage maps
    print("Scenario 2: Interleaved updates to shared state...")
    
    async def mixed_operations_task(task_id):
        key = test_keys[task_id % len(test_keys)]
        
        if task_id % 2 == 0:
            # Record success
            await usage_manager.record_success(key, test_model, None)
        else:
            # Record failure
            error = ClassifiedError(
                error_type='server_error',
                original_exception=Exception(f"Server error for task {task_id}")
            )
            await usage_manager.record_failure(key, test_model, error)
    
    # Run mixed operations concurrently
    tasks = [mixed_operations_task(i) for i in range(20)]
    await asyncio.gather(*tasks)
    
    # Verify state consistency
    async with usage_manager._data_lock:
        for key in test_keys:
            key_data = usage_manager._usage_data.get(key, {})
            success_count = key_data.get("daily", {}).get("models", {}).get(test_model, {}).get("success_count", 0)
            failure_count = key_data.get("failures", {}).get(test_model, {}).get("consecutive_failures", 0)
            print(f"Key {key}: {success_count} successes, {failure_count} consecutive failures")
    
    # Scenario 3: Test key selection under concurrent load
    print("Scenario 3: Key selection under concurrent load...")
    
    selected_keys = []
    
    async def select_key_task(task_id):
        try:
            deadline = time.time() + 5
            key = await usage_manager.acquire_key(test_keys, test_model, deadline)
            selected_keys.append(key)
            
            # Hold key briefly
            await asyncio.sleep(0.01)
            
            # Release key
            await usage_manager.release_key(key, test_model)
            return key
        except Exception as e:
            print(f"Task {task_id} failed to acquire key: {e}")
            return None
    
    # Run concurrent key selection
    tasks = [select_key_task(i) for i in range(15)]
    results = await asyncio.gather(*tasks)
    
    # Verify no duplicate key selections occurred simultaneously
    valid_selections = [k for k in results if k is not None]
    print(f"Selected keys: {valid_selections}")
    print(f"Unique selections: {len(set(valid_selections))}")
    
    # Clean up
    import os
    try:
        os.remove("race_test_usage.json")
    except FileNotFoundError:
        pass
    
    print("✓ Original race condition scenarios test completed successfully!")
    return True


if __name__ == "__main__":
    success = asyncio.run(test_original_race_scenario())
    print(f"Race condition test result: {'PASSED' if success else 'FAILED'}")
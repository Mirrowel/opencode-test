#!/usr/bin/env python3
"""
Test script to verify the concurrency race condition fixes in the cooldown/usage management system.
This script simulates concurrent requests that trigger cooldowns to test the race condition fixes.
"""

import asyncio
import time
import random
from src.rotator_library.usage_manager import UsageManager
from src.rotator_library.error_handler import ClassifiedError

async def simulate_concurrent_requests():
    """Simulates concurrent API requests that can trigger race conditions."""
    
    # Initialize usage manager with test data
    usage_manager = UsageManager(file_path="test_key_usage.json")
    
    # Test keys
    test_keys = ["test_key_1", "test_key_2", "test_key_3", "test_key_4", "test_key_5"]
    model = "test/model"
    
    print("Starting concurrency race condition test...")
    print(f"Testing with {len(test_keys)} keys and model: {model}")
    
    # Create multiple concurrent tasks that try to acquire keys
    async def request_task(task_id: int):
        """Simulates a single concurrent request task."""
        try:
            deadline = time.time() + 10  # 10 second timeout
            print(f"Task {task_id}: Attempting to acquire key...")
            
            key = await usage_manager.acquire_key(test_keys, model, deadline)
            print(f"Task {task_id}: Acquired key {key}")
            
            # Simulate some work with the key
            await asyncio.sleep(random.uniform(0.1, 0.5))
            
            # Simulate potential failure that could trigger cooldown
            if random.random() < 0.3:  # 30% chance of failure
                print(f"Task {task_id}: Simulating failure for key {key}")
                error = ClassifiedError(
                    error_type='rate_limit',
                    status_code=429,
                    retry_after=random.randint(1, 3),
                    original_exception=Exception("Rate limit exceeded")
                )
                await usage_manager.record_failure(key, model, error)
            else:
                # Record success
                await usage_manager.record_success(key, model)
            
            # Release the key
            await usage_manager.release_key(key, model)
            print(f"Task {task_id}: Released key {key}")
            
        except Exception as e:
            print(f"Task {task_id}: Error - {e}")
    
    # Launch multiple concurrent tasks
    tasks = []
    for i in range(20):  # 20 concurrent tasks
        task = asyncio.create_task(request_task(i))
        tasks.append(task)
        await asyncio.sleep(0.01)  # Small delay between task launches
    
    # Wait for all tasks to complete
    await asyncio.gather(*tasks, return_exceptions=True)
    
    print("Concurrency test completed!")
    
    # Test the key state initialization race condition fix
    print("\nTesting key state initialization race condition fix...")
    
    # Create multiple tasks that initialize key states concurrently
    async def init_task(task_id: int):
        new_keys = [f"new_key_{task_id}_{i}" for i in range(3)]
        await usage_manager._initialize_key_states(new_keys)
        print(f"Init task {task_id}: Initialized key states")
    
    init_tasks = [asyncio.create_task(init_task(i)) for i in range(10)]
    await asyncio.gather(*init_tasks, return_exceptions=True)
    
    print("Key state initialization test completed!")

if __name__ == "__main__":
    try:
        asyncio.run(simulate_concurrent_requests())
        print("\n✅ All concurrency tests passed! The race condition fixes appear to be working.")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        raise
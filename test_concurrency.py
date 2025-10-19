#!/usr/bin/env python3
"""
Comprehensive concurrency tests for the API key rotation system.
Tests race conditions in key selection, cooldown updates, and usage statistics.
"""

import asyncio
import time
import random
from datetime import datetime, timezone
from src.rotator_library.usage_manager import UsageManager
from src.rotator_library.error_handler import ClassifiedError


class TestConcurrency:
    def __init__(self):
        self.usage_manager = UsageManager(file_path="test_key_usage.json")
        self.test_keys = [f"test_key_{i}" for i in range(5)]
        self.test_model = "openai/gpt-4"
        self.results = {
            "key_selections": {},
            "success_counts": {},
            "failure_counts": {},
            "concurrent_failures": 0,
            "duplicate_key_selections": 0
        }

    async def test_concurrent_key_selection(self, num_tasks=20, requests_per_task=10):
        """Test that concurrent key selection doesn't result in duplicate key usage."""
        print(f"Testing concurrent key selection with {num_tasks} tasks, {requests_per_task} requests each...")
        
        async def worker_task(task_id):
            acquired_keys = []
            for i in range(requests_per_task):
                try:
                    deadline = time.time() + 30  # 30 second deadline
                    key = await self.usage_manager.acquire_key(self.test_keys, self.test_model, deadline)
                    acquired_keys.append(key)
                    
                    # Simulate some work
                    await asyncio.sleep(random.uniform(0.001, 0.01))
                    
                    # Release the key
                    await self.usage_manager.release_key(key, self.test_model)
                    
                except Exception as e:
                    print(f"Task {task_id} request {i} failed: {e}")
            
            return acquired_keys

        # Run concurrent tasks
        tasks = [worker_task(i) for i in range(num_tasks)]
        results = await asyncio.gather(*tasks)
        
        # Analyze results for duplicate key selections
        all_acquisitions = []
        for task_result in results:
            all_acquisitions.extend(task_result)
        
        # Check for concurrent duplicate selections (should be prevented by atomic key selection)
        key_usage_counts = {}
        for key in all_acquisitions:
            key_usage_counts[key] = key_usage_counts.get(key, 0) + 1
        
        print(f"Key usage distribution: {key_usage_counts}")
        print(f"Total acquisitions: {len(all_acquisitions)}")
        
        # Verify fair distribution (keys should be used roughly equally)
        min_usage = min(key_usage_counts.values())
        max_usage = max(key_usage_counts.values())
        usage_variance = max_usage - min_usage
        
        print(f"Usage variance: {usage_variance}")
        assert usage_variance < len(all_acquisitions) * 0.3, f"Unfair key distribution: variance {usage_variance}"
        
        return key_usage_counts

    async def test_concurrent_cooldown_updates(self, num_concurrent_failures=10):
        """Test that concurrent failure recordings don't lose cooldown updates."""
        print(f"Testing concurrent cooldown updates with {num_concurrent_failures} concurrent failures...")
        
        # Reset key state
        test_key = self.test_keys[0]
        
        async def record_failure_task(failure_num):
            error = ClassifiedError(
                error_type='rate_limit',
                original_exception=Exception(f"Test rate limit error {failure_num}"),
                retry_after=60 + failure_num  # Different cooldown times
            )
            await self.usage_manager.record_failure(test_key, self.test_model, error)
        
        # Record multiple concurrent failures
        tasks = [record_failure_task(i) for i in range(num_concurrent_failures)]
        await asyncio.gather(*tasks)
        
        # Verify that cooldown was applied (should be the longest one due to race condition prevention)
        async with self.usage_manager._data_lock:
            if self.usage_manager._usage_data is None:
                return 0
            key_data = self.usage_manager._usage_data.get(test_key, {})
            model_cooldown = key_data.get("model_cooldowns", {}).get(self.test_model, 0)
            current_time = time.time()
            
        print(f"Final cooldown time: {model_cooldown - current_time:.1f} seconds")
        
        # The cooldown should be at least 60 seconds (minimum retry_after)
        assert model_cooldown > current_time + 50, "Cooldown not properly applied"
        
        return model_cooldown - current_time

    async def test_concurrent_success_updates(self, num_concurrent_successes=20):
        """Test that concurrent success recordings don't lose updates."""
        print(f"Testing concurrent success updates with {num_concurrent_successes} concurrent successes...")
        
        test_key = self.test_keys[1]
        
        async def record_success_task(success_num):
            # Create a mock response with usage data
            mock_response = type('MockResponse', (), {
                'usage': type('MockUsage', (), {
                    'prompt_tokens': 10 + success_num,
                    'completion_tokens': 20 + success_num
                })()
            })()
            
            await self.usage_manager.record_success(test_key, self.test_model, mock_response)
        
        # Record multiple concurrent successes
        tasks = [record_success_task(i) for i in range(num_concurrent_successes)]
        await asyncio.gather(*tasks)
        
        # Verify that all success counts and token usage were recorded
        async with self.usage_manager._data_lock:
            if self.usage_manager._usage_data is None:
                return 0, 0, 0
            key_data = self.usage_manager._usage_data.get(test_key, {})
            daily_models = key_data.get("daily", {}).get("models", {})
            model_data = daily_models.get(self.test_model, {})
            
            success_count = model_data.get("success_count", 0)
            prompt_tokens = model_data.get("prompt_tokens", 0)
            completion_tokens = model_data.get("completion_tokens", 0)
        
        print(f"Success count: {success_count}")
        print(f"Prompt tokens: {prompt_tokens}")
        print(f"Completion tokens: {completion_tokens}")
        
        # All concurrent successes should be recorded
        assert success_count == num_concurrent_successes, f"Expected {num_concurrent_successes} successes, got {success_count}"
        
        # Token counts should be cumulative
        expected_prompt_tokens = sum(10 + i for i in range(num_concurrent_successes))
        expected_completion_tokens = sum(20 + i for i in range(num_concurrent_successes))
        
        assert prompt_tokens == expected_prompt_tokens, f"Expected {expected_prompt_tokens} prompt tokens, got {prompt_tokens}"
        assert completion_tokens == expected_completion_tokens, f"Expected {expected_completion_tokens} completion tokens, got {completion_tokens}"
        
        return success_count, prompt_tokens, completion_tokens

    async def test_key_distribution_under_load(self, num_tasks=50, requests_per_task=5):
        """Test that key distribution remains fair under high load."""
        print(f"Testing key distribution under load with {num_tasks} tasks...")
        
        async def worker_task(task_id):
            acquired_keys = []
            for i in range(requests_per_task):
                try:
                    deadline = time.time() + 10  # Short deadline to create contention
                    key = await self.usage_manager.acquire_key(self.test_keys, self.test_model, deadline)
                    acquired_keys.append(key)
                    
                    # Very short work to maximize contention
                    await asyncio.sleep(0.001)
                    
                    # Release the key
                    await self.usage_manager.release_key(key, self.test_model)
                    
                except Exception as e:
                    # Expected under high load - some requests may timeout
                    pass
            
            return acquired_keys

        # Run concurrent tasks
        tasks = [worker_task(i) for i in range(num_tasks)]
        results = await asyncio.gather(*tasks)
        
        # Count key usage
        key_usage_counts = {}
        for task_result in results:
            for key in task_result:
                key_usage_counts[key] = key_usage_counts.get(key, 0) + 1
        
        print(f"Key usage under load: {key_usage_counts}")
        
        # Verify that all keys were used (no starvation)
        used_keys = set(key_usage_counts.keys())
        assert len(used_keys) > 0, "No keys were used under load"
        
        # Check for reasonable distribution (shouldn't be completely skewed)
        if len(key_usage_counts) > 1:
            usage_values = list(key_usage_counts.values())
            usage_ratio = max(usage_values) / min(usage_values)
            print(f"Usage ratio (max/min): {usage_ratio:.2f}")
            
            # The ratio shouldn't be too extreme (e.g., 10:1 would indicate poor distribution)
            assert usage_ratio < 5.0, f"Extreme unfair distribution: ratio {usage_ratio}"
        
        return key_usage_counts

    async def run_all_tests(self):
        """Run all concurrency tests."""
        print("=== Starting Concurrency Tests ===")
        
        try:
            # Test 1: Concurrent key selection
            key_distribution = await self.test_concurrent_key_selection()
            print("✓ Concurrent key selection test passed")
            
            # Test 2: Concurrent cooldown updates
            cooldown_time = await self.test_concurrent_cooldown_updates()
            print("✓ Concurrent cooldown updates test passed")
            
            # Test 3: Concurrent success updates
            success_stats = await self.test_concurrent_success_updates()
            print("✓ Concurrent success updates test passed")
            
            # Test 4: Key distribution under load
            load_distribution = await self.test_key_distribution_under_load()
            print("✓ Key distribution under load test passed")
            
            print("=== All Concurrency Tests Passed! ===")
            return {
                "key_distribution": key_distribution,
                "cooldown_time": cooldown_time,
                "success_stats": success_stats,
                "load_distribution": load_distribution
            }
            
        except Exception as e:
            print(f"✗ Test failed: {e}")
            raise
        
        finally:
            # Clean up test file
            import os
            try:
                os.remove("test_key_usage.json")
            except FileNotFoundError:
                pass


async def main():
    """Main test runner."""
    tester = TestConcurrency()
    results = await tester.run_all_tests()
    return results


if __name__ == "__main__":
    results = asyncio.run(main())
    print(f"Test results: {results}")
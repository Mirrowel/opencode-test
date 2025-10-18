#!/usr/bin/env python3
"""
Test script for token expiration tracking functionality
"""

import json
import base64
import time
from datetime import datetime, timezone

# Test JWT token parsing
def create_test_jwt_token(exp_timestamp):
    """Create a test JWT token with specified expiration"""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": "test_user",
        "exp": exp_timestamp,
        "iat": int(time.time())
    }
    
    # Encode header and payload
    header_encoded = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip('=')
    payload_encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    
    # Create signature (dummy for testing)
    signature = "dummy_signature"
    
    return f"{header_encoded}.{payload_encoded}.{signature}"

def parse_jwt_token_expiry(token: str) -> float:
    """Parse JWT token to extract expiration timestamp"""
    try:
        # JWT tokens have 3 parts separated by dots
        parts = token.split('.')
        if len(parts) != 3:
            return None
        
        # Decode the payload (second part)
        payload = parts[1]
        # Add padding if needed
        payload += '=' * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        payload_data = json.loads(decoded)
        
        # Get expiration time (exp claim)
        exp = payload_data.get('exp')
        if exp:
            return float(exp)
        return None
    except Exception as e:
        print(f"Error parsing JWT: {e}")
        return None

def is_token_expiring_soon(token: str, skew_seconds: int = 120) -> bool:
    """Check if token will expire within the skew window"""
    exp = parse_jwt_token_expiry(token)
    if not exp:
        return False
    
    current_time = time.time()
    return exp - current_time <= skew_seconds

def test_token_expiration_tracking():
    """Test the token expiration tracking functionality"""
    print("Testing token expiration tracking...")
    
    # Test 1: Token expiring soon (within 60 seconds)
    exp_time = int(time.time()) + 60  # 60 seconds from now
    token = create_test_jwt_token(exp_time)
    
    print(f"Created token expiring at: {datetime.fromtimestamp(exp_time, timezone.utc)}")
    print(f"Current time: {datetime.now(timezone.utc)}")
    
    exp_parsed = parse_jwt_token_expiry(token)
    print(f"Parsed expiration: {datetime.fromtimestamp(exp_parsed, timezone.utc)}")
    
    is_expiring = is_token_expiring_soon(token, 120)  # 2 minute skew
    print(f"Is token expiring soon (120s skew)? {is_expiring}")
    
    # Test 2: Token expiring later (5 minutes from now)
    exp_time_later = int(time.time()) + 300  # 5 minutes from now
    token_later = create_test_jwt_token(exp_time_later)
    
    is_expiring_later = is_token_expiring_soon(token_later, 120)
    print(f"Is later token expiring soon (120s skew)? {is_expiring_later}")
    
    # Test 3: Token already expired
    exp_time_past = int(time.time()) - 60  # 1 minute ago
    token_past = create_test_jwt_token(exp_time_past)
    
    is_expiring_past = is_token_expiring_soon(token_past, 120)
    print(f"Is expired token expiring soon (120s skew)? {is_expiring_past}")
    
    print("\nTest completed successfully!")

if __name__ == "__main__":
    test_token_expiration_tracking()
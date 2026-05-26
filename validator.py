import os
import time
import threading
import requests
from email_validator import validate_email, EmailNotValidError

_DNS_CACHE = {}
_API_LOCK = threading.Lock()
_LAST_API_CALL = 0.0

def check_email_validity(email, use_api=True):
    """
    Checks if an email is valid using AbstractAPI if an API key is present and use_api is True.
    If no API key is present or use_api is False, it falls back to strict Syntax + DNS (MX) check.
    Returns (True, "") if valid, (False, "reason") if invalid.
    """
    email = email.strip()
    if not email:
        return False, "Empty email"
        
    api_key = os.getenv("EMAIL_API_KEY") if use_api else None
    global _LAST_API_CALL
    
    if api_key:
        url = f"https://emailvalidation.abstractapi.com/v1/?api_key={api_key}&email={email}"
        
        with _API_LOCK:
            # AbstractAPI free tier is strictly 1 request per second.
            now = time.time()
            elapsed = now - _LAST_API_CALL
            if elapsed < 1.1:
                time.sleep(1.1 - elapsed)
                
            try:
                response = requests.get(url, timeout=10)
                _LAST_API_CALL = time.time()
                
                if response.status_code == 200:
                    data = response.json()
                    deliverability = data.get("deliverability")
                    
                    if deliverability == "UNDELIVERABLE":
                        return False, "API verified as Undeliverable (Bounces)"
                    
                    if data.get("is_valid_format", {}).get("value") is False:
                        return False, "API verified as invalid format"
                        
                    return True, "Valid"
                elif response.status_code == 429:
                    print(f"API Rate limit exceeded for {email} despite delay. Falling back to local DNS check.")
                else:
                    print(f"API Error {response.status_code} for {email}. Falling back to local DNS check.")
            except Exception as e:
                print(f"Network error with API: {str(e)}. Falling back to local DNS check.")

    # Domain-level cache for DNS checks to prevent UDP packet loss during high concurrency
    global _DNS_CACHE
    if '_DNS_CACHE' not in globals():
        _DNS_CACHE = {}
        
    domain = email.split('@')[-1].lower() if '@' in email else ''
    
    if domain in _DNS_CACHE:
        if _DNS_CACHE[domain] is True:
            return True, "Valid"
        else:
            return False, _DNS_CACHE[domain + "_error"]

    # Local fallback: Syntax + DNS (MX Record) check using email_validator
    try:
        # check_deliverability=True performs a robust DNS check to ensure the domain actually accepts mail
        valid = validate_email(email, check_deliverability=True)
        _DNS_CACHE[domain] = True
        return True, "Valid"
    except EmailNotValidError as e:
        _DNS_CACHE[domain] = False
        _DNS_CACHE[domain + "_error"] = f"Invalid: {str(e)}"
        return False, f"Invalid: {str(e)}"

import os
import requests
from email_validator import validate_email, EmailNotValidError

def check_email_validity(email):
    """
    Checks if an email is valid using AbstractAPI if an API key is present.
    If no API key is present, it falls back to strict Syntax + DNS (MX) check.
    Returns (True, "") if valid, (False, "reason") if invalid.
    """
    email = email.strip()
    if not email:
        return False, "Empty email"
        
    api_key = os.getenv("EMAIL_API_KEY") # AbstractAPI key
    
    if api_key:
        url = f"https://emailvalidation.abstractapi.com/v1/?api_key={api_key}&email={email}"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                deliverability = data.get("deliverability")
                
                # AbstractAPI deliverability can be: DELIVERABLE, UNDELIVERABLE, RISKY, UNKNOWN
                if deliverability == "UNDELIVERABLE":
                    return False, "API verified as Undeliverable (Bounces)"
                
                if data.get("is_valid_format", {}).get("value") is False:
                    return False, "API verified as invalid format"
                    
                # We treat DELIVERABLE, RISKY, and UNKNOWN as 'valid' so we don't accidentally
                # highlight emails that are technically fine but just use a catch-all server.
                return True, "Valid"
            elif response.status_code == 429:
                print(f"API Rate limit exceeded for {email}. Falling back to local DNS check.")
            else:
                print(f"API Error {response.status_code} for {email}. Falling back to local DNS check.")
        except Exception as e:
            print(f"Network error with API: {str(e)}. Falling back to local DNS check.")

    # Local fallback: Syntax + DNS (MX Record) check using email_validator
    try:
        # check_deliverability=True performs a robust DNS check to ensure the domain actually accepts mail
        valid = validate_email(email, check_deliverability=True)
        return True, "Valid"
    except EmailNotValidError as e:
        return False, f"Invalid: {str(e)}"

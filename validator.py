import os
import time
import threading
import requests
import smtplib
import socket
import dns.resolver
from email_validator import validate_email, EmailNotValidError

_DNS_CACHE = {}
_API_LOCK = threading.Lock()
_LAST_API_CALL = 0.0
_DISPOSABLE_DOMAINS = set()
_DISPOSABLE_LOADED = False

COMMON_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com"
}

def load_disposable_domains():
    global _DISPOSABLE_LOADED, _DISPOSABLE_DOMAINS
    if _DISPOSABLE_LOADED:
        return
    try:
        url = "https://raw.githubusercontent.com/disposable/disposable-email-domains/master/domains.txt"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            domains = resp.text.splitlines()
            _DISPOSABLE_DOMAINS = {d.strip().lower() for d in domains if d.strip()}
    except Exception:
        pass
    finally:
        _DISPOSABLE_LOADED = True

def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def correct_typo(email):
    """
    Checks if the domain is extremely close to a common provider (distance <= 2).
    Returns the corrected email if a typo is found, else None.
    """
    if '@' not in email:
        return None
    local, domain = email.split('@', 1)
    domain = domain.lower()
    
    if domain in COMMON_DOMAINS:
        return None
        
    for common in COMMON_DOMAINS:
        if len(domain) > 3 and levenshtein_distance(domain, common) <= 2:
            # We don't want to accidentally correct valid custom domains that look similar.
            # Usually only common typos are caught. Distance <= 2 is safe.
            return f"{local}@{common}"
    return None

def get_mx_records(domain):
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        return sorted([(rdata.preference, rdata.exchange.to_text().strip('.')) for rdata in answers])
    except Exception:
        return []

def check_smtp_handshake(email):
    """
    Performs Tier 2 Direct SMTP handshake and catch-all detection.
    Returns (is_valid, reason) or throws Exception if it times out/network error.
    """
    domain = email.split('@')[-1]
    mx_records = get_mx_records(domain)
    
    if not mx_records:
        return False, "SMTP: No MX records found."
        
    # Try the highest priority MX server
    server = mx_records[0][1]
    
    try:
        with smtplib.SMTP(server, port=25, timeout=5) as smtp:
            smtp.ehlo_or_helo_if_needed()
            smtp.mail('bot@email-validator.local')
            
            # Check target email
            code, message = smtp.rcpt(email)
            if code not in (250, 251):
                return False, f"SMTP Bounced: {code} {message.decode('utf-8', 'ignore')}"
                
            # It accepted the email. Now let's check for catch-all.
            fake_email = f"dsfsdfkhsdkfhjsdf@{domain}"
            code, message = smtp.rcpt(fake_email)
            if code in (250, 251):
                return False, "SMTP: Server is a Catch-All (Risky)"
                
            return True, "Valid"
    except socket.timeout:
        raise Exception("SMTP Connection Timed Out (Port 25 blocked?)")
    except Exception as e:
        raise Exception(f"SMTP Error: {str(e)}")

def check_email_validity(email, use_api=True):
    """
    Returns (is_valid, reason, corrected_email)
    """
    email = email.strip()
    if not email:
        return False, "Empty email", None
        
    # 1. Typo Correction
    corrected_email = correct_typo(email)
    email_to_check = corrected_email if corrected_email else email
    
    # 2. Disposable Email Check
    load_disposable_domains()
    domain = email_to_check.split('@')[-1].lower() if '@' in email_to_check else ''
    if domain in _DISPOSABLE_DOMAINS:
        return False, "Disposable/Burner Email", corrected_email

    # 3. Tier 1: AbstractAPI (if enabled)
    api_key = os.getenv("EMAIL_API_KEY") if use_api else None
    global _LAST_API_CALL
    
    if api_key:
        url = f"https://emailvalidation.abstractapi.com/v1/?api_key={api_key}&email={email_to_check}"
        with _API_LOCK:
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
                        return False, "API verified as Undeliverable", corrected_email
                    if data.get("is_valid_format", {}).get("value") is False:
                        return False, "API verified as invalid format", corrected_email
                    return True, "Valid", corrected_email
                elif response.status_code == 429:
                    print(f"API Rate limit exceeded for {email_to_check}. Falling back to Tier 2.")
                else:
                    print(f"API Error {response.status_code} for {email_to_check}. Falling back to Tier 2.")
            except Exception as e:
                print(f"Network error with API: {str(e)}. Falling back to Tier 2.")

    # 4. Tier 2: Direct SMTP Handshake (Catch-All detection)
    try:
        is_valid, reason = check_smtp_handshake(email_to_check)
        return is_valid, reason, corrected_email
    except Exception as e:
        print(f"Tier 2 failed for {email_to_check}: {e}. Falling back to Tier 3.")
        
    # 5. Tier 3: Local DNS Fallback & Cache
    global _DNS_CACHE
    if domain in _DNS_CACHE:
        if _DNS_CACHE[domain] is True:
            return True, "Valid", corrected_email
        else:
            return False, _DNS_CACHE[domain + "_error"], corrected_email

    try:
        valid = validate_email(email_to_check, check_deliverability=True)
        _DNS_CACHE[domain] = True
        return True, "Valid", corrected_email
    except EmailNotValidError as e:
        _DNS_CACHE[domain] = False
        _DNS_CACHE[domain + "_error"] = f"Invalid: {str(e)}"
        return False, f"Invalid: {str(e)}", corrected_email

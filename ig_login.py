"""
One-time Instagram login — run this on your PC to generate ig_session.json.
After running, add ig_session.json contents as GitHub secret IG_SESSION_JSON.

Usage:
  python ig_login.py
"""

from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired
from pathlib import Path

USERNAME = input("Instagram username: ").strip()
PASSWORD = input("Instagram password: ").strip()

cl = Client()

print("\nLogging in...")
try:
    cl.login(USERNAME, PASSWORD)
except TwoFactorRequired:
    print("\nInstagram requires 2FA verification.")
    code = input("Enter the 6-digit code from your authenticator app or SMS: ").strip()
    cl.login(USERNAME, PASSWORD, verification_code=code)

session_file = Path("ig_session.json")
cl.dump_settings(str(session_file))
print(f"\nSession saved: {session_file}")

print("\n--- Add these as GitHub secrets ---")
print(f"IG_USERNAME    : {USERNAME}")
print(f"IG_PASSWORD    : (your password)")
print(f"IG_SESSION_JSON: (copy the full contents of ig_session.json)")

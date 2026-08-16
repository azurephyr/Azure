#!/usr/bin/env python3
"""
Generate secure credentials for Azure web dashboard.

Usage:
    python scripts/generate_credentials.py

This will generate:
1. A secure JWT secret key (AZURE_WEB_SECRET)
2. A bcrypt password hash (AZURE_ADMIN_PASSWORD_HASH)

Add the output to your .env file.
"""
import secrets
from getpass import getpass

from passlib.context import CryptContext


def main():
    print("=" * 70)
    print("Azure Web Dashboard Credential Generator")
    print("=" * 70)
    print()

    # Generate JWT secret
    jwt_secret = secrets.token_urlsafe(32)
    print("1. JWT Secret Key (for token signing):")
    print(f"   AZURE_WEB_SECRET={jwt_secret}")
    print()

    # Generate password hash
    print("2. Admin Password:")
    password = getpass("   Enter admin password: ")
    password_confirm = getpass("   Confirm password: ")

    if password != password_confirm:
        print("   ❌ Passwords don't match!")
        return

    if len(password) < 8:
        print("   ⚠️  Warning: Password is short. Recommend 12+ characters.")

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd_context.hash(password)

    print()
    print(f"   AZURE_ADMIN_PASSWORD_HASH={password_hash}")
    print()

    # Summary
    print("=" * 70)
    print("✅ Credentials generated successfully!")
    print("=" * 70)
    print()
    print("Add these lines to your .env file:")
    print()
    print(f"AZURE_WEB_SECRET={jwt_secret}")
    print(f"AZURE_ADMIN_PASSWORD_HASH={password_hash}")
    print()
    print("⚠️  Keep these credentials secure and never commit them to git!")
    print("=" * 70)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
    except Exception as e:
        print(f"\n❌ Error: {e}")

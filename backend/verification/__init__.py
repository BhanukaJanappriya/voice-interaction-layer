"""Verification wizard backend (plan 3.3 R3): a name and landline lookup in
a local customer directory, then a local one time code (120 s, single use,
3 attempts) that ends in a user_session cookie with the claim amr ["otp"].

Mount `verification.routes.router` (prefix /verify) in server.py. Models are
in verification.models; the Alembic revision is
backend/db/migrations/versions/20261009_0003_verification.py. Self check: python -m verification
"""

"""
Single entry point for reading secrets from AWS Secrets Manager.
Every script and the agent entrypoint should import from here —
never call boto3's secretsmanager client directly anywhere else.
"""
import json
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_PROFILE = os.environ.get("AWS_PROFILE")  # None in production; set locally


def _get_client():
    if _PROFILE:
        session = boto3.Session(profile_name=_PROFILE)
    else:
        session = boto3.Session()
    return session.client("secretsmanager", region_name=_REGION)


@lru_cache(maxsize=32)
def get_secret(secret_id: str) -> dict:
    """
    Fetch and cache a secret's value for the life of the process.
    Returns a dict if the secret was stored as key/value pairs, or
    {'value': <string>} if it was stored as plaintext.
    """
    client = _get_client()
    try:
        response = client.get_secret_value(SecretId=secret_id)
    except ClientError as e:
        raise RuntimeError(f"Could not read secret '{secret_id}': {e}") from e

    raw = response["SecretString"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"value": raw}


def get_secret_value(secret_id: str, key: str | None = None):
    """Convenience wrapper: get_secret_value('agentcore-eval/judge-model-config', 'judge_model_id')"""
    secret = get_secret(secret_id)
    if key is None:
        return secret
    return secret[key]
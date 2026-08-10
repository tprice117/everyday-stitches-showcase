"""Config access via AWS SSM Parameter Store, with an environment-variable
fallback so the functions run locally without AWS.

A parameter name like "/sales-sync/woo/consumer_key" maps to the env var
SALES_SYNC_WOO_CONSUMER_KEY for local development. In Lambda, boto3 reads the
real Parameter Store value.
"""
import os

try:
    import boto3
except ImportError:          # boto3 isn't installed locally; that's fine
    boto3 = None

_client = None


def _ssm():
    global _client
    if _client is None:
        _client = boto3.client("ssm")
    return _client


def _env_key(name):
    return name.strip("/").replace("/", "_").replace("-", "_").upper()


def get_param(name, default=None):
    """Return a parameter value. Prefers a local env override, then Parameter
    Store, then the provided default."""
    env = _env_key(name)
    if env in os.environ:
        return os.environ[env]
    if boto3 is not None:
        try:
            resp = _ssm().get_parameter(Name=name, WithDecryption=True)
            return resp["Parameter"]["Value"]
        except Exception:
            pass
    return default


def put_param(name, value):
    """Persist a parameter (e.g. the advanced watermark or a rotated token).
    Locally, just updates the process env so a test run stays self-contained."""
    if boto3 is not None:
        _ssm().put_parameter(Name=name, Value=str(value), Type="String", Overwrite=True)
    else:
        os.environ[_env_key(name)] = str(value)

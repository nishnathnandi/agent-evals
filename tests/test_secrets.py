"""Tests for the secrets module."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path so we can import from it
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.secrets import get_secret, get_secret_value


class TestGetSecret:
    """Tests for get_secret function."""

    def test_get_secret_with_json_value(self):
        """Test fetching a secret stored as JSON key-value pairs."""
        mock_client = MagicMock()
        secret_data = {"key1": "value1", "key2": "value2"}
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps(secret_data)
        }

        with patch("src.secrets._get_client", return_value=mock_client):
            # Clear the cache to ensure fresh call
            get_secret.cache_clear()
            result = get_secret("test-secret")

        print(f"\n✓ JSON Secret Retrieved: {result}")
        assert result == secret_data
        mock_client.get_secret_value.assert_called_once_with(SecretId="test-secret")

    def test_get_secret_with_plaintext_value(self):
        """Test fetching a secret stored as plaintext."""
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "plain-secret-value"}

        with patch("src.secrets._get_client", return_value=mock_client):
            get_secret.cache_clear()
            result = get_secret("test-secret")

        print(f"✓ Plaintext Secret Retrieved: {result}")
        assert result == {"value": "plain-secret-value"}

    def test_get_secret_caching(self):
        """Test that secrets are cached after first retrieval."""
        mock_client = MagicMock()
        secret_data = {"cached": "data"}
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps(secret_data)
        }

        with patch("src.secrets._get_client", return_value=mock_client):
            get_secret.cache_clear()
            result1 = get_secret("test-secret")
            result2 = get_secret("test-secret")

        print(f"✓ Cached Secret (1st call): {result1}")
        print(f"✓ Cached Secret (2nd call): {result2}")
        assert result1 == result2
        # Should only be called once due to caching
        mock_client.get_secret_value.assert_called_once()

    def test_get_secret_client_error(self):
        """Test error handling when AWS API fails."""
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
        )

        with patch("src.secrets._get_client", return_value=mock_client):
            get_secret.cache_clear()
            with pytest.raises(RuntimeError, match="Could not read secret"):
                get_secret("non-existent-secret")


class TestGetSecretValue:
    """Tests for get_secret_value convenience wrapper."""

    def test_get_secret_value_with_key(self):
        """Test fetching a specific key from a secret."""
        mock_client = MagicMock()
        secret_data = {"judge_model_id": "claude-3-sonnet", "other_key": "other_value"}
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps(secret_data)
        }

        with patch("src.secrets._get_client", return_value=mock_client):
            get_secret.cache_clear()
            result = get_secret_value("agentcore-eval/judge-model-config", "judge_model_id")

        print(f"✓ Secret Value (by key): {result}")
        assert result == "claude-3-sonnet"

    def test_get_secret_value_without_key(self):
        """Test fetching the entire secret when no key specified."""
        mock_client = MagicMock()
        secret_data = {"key1": "value1", "key2": "value2"}
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps(secret_data)
        }

        with patch("src.secrets._get_client", return_value=mock_client):
            get_secret.cache_clear()
            result = get_secret_value("test-secret")

        print(f"✓ Secret Value (entire secret): {result}")
        assert result == secret_data

    def test_get_secret_value_missing_key(self):
        """Test error when requested key doesn't exist in secret."""
        mock_client = MagicMock()
        secret_data = {"key1": "value1"}
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps(secret_data)
        }

        with patch("src.secrets._get_client", return_value=mock_client):
            get_secret.cache_clear()
            with pytest.raises(KeyError):
                get_secret_value("test-secret", "nonexistent_key")

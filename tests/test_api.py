import os
from unittest.mock import patch, MagicMock

import pytest
import requests

from dracs.api import dell_api_warranty_date
from dracs.exceptions import APIError, ValidationError


class TestDellApiWarrantyDate:
    def test_empty_list_raises(self):
        with pytest.raises(ValidationError, match="At least one service tag"):
            dell_api_warranty_date([])

    def test_missing_credentials_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(APIError, match="Dell API credentials not found"):
                dell_api_warranty_date("ABC1234")

    @patch("dracs.api.requests.get")
    @patch("dracs.api.requests.post")
    def test_single_tag_success(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(json=lambda: {"access_token": "fake-token"})
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {
                    "serviceTag": "ABC1234",
                    "entitlements": [
                        {"endDate": "2027-01-15T00:00:00Z"},
                        {"endDate": "2025-06-01T00:00:00Z"},
                    ],
                }
            ],
        )

        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            results = dell_api_warranty_date("ABC1234")

        assert "ABC1234" in results
        epoch, date_str = results["ABC1234"]
        assert epoch > 0
        assert "2027" in date_str

    @patch("dracs.api.requests.get")
    @patch("dracs.api.requests.post")
    def test_list_of_tags(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(json=lambda: {"access_token": "fake-token"})
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {
                    "serviceTag": "TAG0001",
                    "entitlements": [
                        {"endDate": "2027-01-15T00:00:00Z"},
                    ],
                },
                {
                    "serviceTag": "TAG0002",
                    "entitlements": [
                        {"endDate": "2026-06-01T00:00:00Z"},
                    ],
                },
            ],
        )

        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            results = dell_api_warranty_date(["TAG0001", "TAG0002"])

        assert len(results) == 2
        assert "TAG0001" in results
        assert "TAG0002" in results

    @patch("dracs.api.requests.get")
    @patch("dracs.api.requests.post")
    def test_api_failure_raises(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(json=lambda: {"access_token": "fake-token"})
        mock_get.return_value = MagicMock(
            status_code=500,
            text="Internal Server Error",
        )

        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            with pytest.raises(APIError, match="Dell API request failed"):
                dell_api_warranty_date("ABC1234")

    @patch("dracs.api.requests.get")
    @patch("dracs.api.requests.post")
    def test_picks_latest_entitlement(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(json=lambda: {"access_token": "fake-token"})
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {
                    "serviceTag": "ABC1234",
                    "entitlements": [
                        {"endDate": "2020-01-01T00:00:00Z"},
                        {"endDate": "2030-12-31T00:00:00Z"},
                        {"endDate": "2025-06-15T00:00:00Z"},
                    ],
                }
            ],
        )

        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            results = dell_api_warranty_date("ABC1234")

        epoch, date_str = results["ABC1234"]
        assert "2030" in date_str

    @patch(
        "dracs.api.requests.post",
        side_effect=requests.exceptions.Timeout("Connection timed out"),
    )
    def test_auth_timeout_raises(self, mock_post):
        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            with pytest.raises(
                APIError, match="Dell API authentication request timed out"
            ):
                dell_api_warranty_date("ABC1234")

    @patch(
        "dracs.api.requests.post",
        side_effect=requests.exceptions.ConnectionError("Connection refused"),
    )
    def test_auth_connection_error_raises(self, mock_post):
        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            with pytest.raises(
                APIError,
                match="Failed to connect to Dell API authentication server",
            ):
                dell_api_warranty_date("ABC1234")

    @patch(
        "dracs.api.requests.get",
        side_effect=requests.exceptions.Timeout("Connection timed out"),
    )
    @patch("dracs.api.requests.post")
    def test_warranty_timeout_raises(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(json=lambda: {"access_token": "fake-token"})

        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            with pytest.raises(APIError, match="Dell API warranty request timed out"):
                dell_api_warranty_date("ABC1234")

    @patch(
        "dracs.api.requests.get",
        side_effect=requests.exceptions.ConnectionError("Connection refused"),
    )
    @patch("dracs.api.requests.post")
    def test_warranty_connection_error_raises(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(json=lambda: {"access_token": "fake-token"})

        with patch.dict(
            os.environ,
            {"CLIENT_ID": "test-id", "CLIENT_SECRET": "test-secret"},
        ):
            with pytest.raises(
                APIError,
                match="Failed to connect to Dell API warranty server",
            ):
                dell_api_warranty_date("ABC1234")

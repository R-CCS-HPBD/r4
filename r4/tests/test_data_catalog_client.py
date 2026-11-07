"""Tests for R4 DataCatalogClient."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.data_catalog import DataCatalogClient


def test_data_catalog_list_merges_ready_and_pending() -> None:
    client = MagicMock()
    client.config.gateway_url = "http://gateway"
    client._api_prefix = "/gw"
    client.get = MagicMock(
        side_effect=[
            {
                "results": [
                    {"s3_key": "proj-1/a.csv", "state": "ready", "summary": "a"},
                ]
            },
            {
                "results": [
                    {"s3_key": "proj-1/b.csv", "state": "pending", "summary": "b"},
                    {"s3_key": "proj-1/a.csv", "state": "pending", "summary": "dup"},
                ]
            },
        ]
    )
    catalog = DataCatalogClient(client)

    items = catalog.list("proj-1")

    assert len(items) == 2
    keys = {item["s3_key"] for item in items}
    assert keys == {"proj-1/a.csv", "proj-1/b.csv"}
    assert client.get.call_count == 2


def test_data_catalog_search_builds_url() -> None:
    client = MagicMock()
    client.config.gateway_url = "http://gateway"
    client._api_prefix = "/gw"
    client.get.return_value = {"results": []}
    catalog = DataCatalogClient(client)

    catalog.search(query="batch", modality="structured", limit=5)

    client.get.assert_called_once_with(
        "http://gateway/gw/catalog/search",
        params={"q": "batch", "limit": 5, "modality": "structured"},
    )


def test_data_catalog_stats_builds_url() -> None:
    client = MagicMock()
    client.config.gateway_url = "http://gateway"
    client._api_prefix = "/gw"
    client.get.return_value = {"object_count": 3}
    catalog = DataCatalogClient(client)

    result = catalog.stats()

    assert result["object_count"] == 3
    client.get.assert_called_once_with("http://gateway/gw/catalog/stats")


def test_data_catalog_read_builds_url() -> None:
    client = MagicMock()
    client.config.gateway_url = "http://gateway"
    client._api_prefix = "/gw"
    client.get.return_value = {"s3_key": "p/a.csv", "text": "x"}
    catalog = DataCatalogClient(client)

    result = catalog.read("p/a.csv", offset=10, length=500, source="extracted")

    assert result["s3_key"] == "p/a.csv"
    client.get.assert_called_once_with(
        "http://gateway/gw/catalog/read",
        params={"s3_key": "p/a.csv", "offset": 10, "length": 500, "source": "extracted"},
    )


def test_data_catalog_read_rejects_bad_source() -> None:
    client = MagicMock()
    client.config.gateway_url = "http://gateway"
    client._api_prefix = "/gw"
    catalog = DataCatalogClient(client)

    with pytest.raises(Exception, match="source must be"):
        catalog.read("p/a.csv", source="bogus")

    client.get.assert_not_called()

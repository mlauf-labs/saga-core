"""Unit tests for converters configuration loading and the registry."""

from __future__ import annotations

import pytest

from saga.converters import load_converters_config
from saga.converters.config import ConvertersConfig, ServiceConfig
from saga.converters.registry import ConverterRegistry
from saga.converters.router import RoutingConfig
from saga.core.errors import ConfigError


def test_load_converters_config_from_repo() -> None:
    config = load_converters_config("config")
    assert "docling" in config.services
    assert "kreuzberg" in config.services
    assert config.routing.default == "kreuzberg"
    assert config.routing.by_extension["pdf"] == "docling"


def _config() -> ConvertersConfig:
    return ConvertersConfig(
        services={
            "docling": ServiceConfig(base_url="http://docling:5001"),
            "kreuzberg": ServiceConfig(base_url="http://kreuzberg:8000"),
        },
        routing=RoutingConfig(
            default="kreuzberg",
            by_extension={"pdf": "docling"},
            by_mime_type={"application/pdf": "docling"},
        ),
    )


async def test_registry_routes_pdf_to_docling() -> None:
    registry = ConverterRegistry(_config())
    converter = registry.resolve(filename="invoice.pdf")
    assert converter.name == "docling"
    await registry.aclose()


async def test_registry_routes_other_to_kreuzberg() -> None:
    registry = ConverterRegistry(_config())
    converter = registry.resolve(filename="notes.docx", mime_type="application/x")
    assert converter.name == "kreuzberg"
    await registry.aclose()


def test_registry_rejects_unknown_routing_target() -> None:
    config = _config()
    config.routing.by_extension["xyz"] = "ghost"
    with pytest.raises(ConfigError):
        ConverterRegistry(config)


def test_registry_rejects_unknown_service_client() -> None:
    config = ConvertersConfig(
        services={"mystery": ServiceConfig(base_url="http://x")},
        routing=RoutingConfig(default="mystery"),
    )
    with pytest.raises(ConfigError):
        ConverterRegistry(config)

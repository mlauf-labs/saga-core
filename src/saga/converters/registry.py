"""Converter registry: builds service clients from config and resolves the right
converter for a document via the routing rules (FR-3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saga.converters.docling import DoclingConverter
from saga.converters.kreuzberg import KreuzbergConverter
from saga.converters.router import ConverterRouter
from saga.core.errors import ConfigError
from saga.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from saga.converters.base import Converter
    from saga.converters.config import ConvertersConfig, ServiceConfig

_log = get_logger("saga.converters.registry")

# Maps a service name to a factory that builds its client from a ServiceConfig.
_CLIENT_TYPES: dict[str, Callable[[ServiceConfig], Converter]] = {
    "docling": DoclingConverter,
    "kreuzberg": KreuzbergConverter,
}


class ConverterRegistry:
    """Holds converter clients and routes documents to the configured service."""

    def __init__(self, config: ConvertersConfig) -> None:
        self._router = ConverterRouter(config.routing)
        self._converters: dict[str, Converter] = {}
        for name, service in config.services.items():
            self._converters[name] = _build_converter(name, service)
        self._validate_routing(config)

    def _validate_routing(self, config: ConvertersConfig) -> None:
        referenced = {
            config.routing.default,
            *config.routing.by_extension.values(),
            *config.routing.by_mime_type.values(),
        }
        missing = referenced - set(self._converters)
        if missing:
            raise ConfigError(
                f"converters.yaml routing references undefined service(s): "
                f"{sorted(missing)}. Define them under 'services'."
            )

    def resolve(self, *, filename: str, mime_type: str | None = None) -> Converter:
        """Return the converter client for ``filename``/``mime_type`` (FR-3)."""
        service_name = self._router.resolve(filename=filename, mime_type=mime_type)
        converter = self._converters.get(service_name)
        if converter is None:  # pragma: no cover - guarded by _validate_routing
            raise ConfigError(f"No converter client registered for '{service_name}'.")
        _log.debug("routed", filename=filename, service=service_name)
        return converter

    async def aclose(self) -> None:
        """Close all underlying converter clients."""
        for converter in self._converters.values():
            await converter.aclose()


def _build_converter(name: str, service: ServiceConfig) -> Converter:
    client_type = _CLIENT_TYPES.get(name)
    if client_type is None:
        raise ConfigError(
            f"Unknown conversion service '{name}' in converters.yaml. "
            f"Supported services: {sorted(_CLIENT_TYPES)}."
        )
    return client_type(service)

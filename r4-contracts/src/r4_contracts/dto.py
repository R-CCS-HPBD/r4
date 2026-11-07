"""Dataclass-based API DTO helper.

Stdlib-only alternative to Pydantic for shared contract models. The core use
case is `from_api(dict) -> DTO` and `to_dict() -> dict`, with optional field
aliases for API keys that differ from Python names.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from typing import Any, ClassVar

__all__ = ["APIModel"]


class APIModel:
    """Mixin for dataclass-based API DTOs.

    Example:
        @dataclass
        class Foo(APIModel):
            name: str
            created_at: str = ""
            _api_aliases: ClassVar[dict[str, str]] = {"created_at": "createdAt"}
    """

    _api_aliases: ClassVar[dict[str, str]] = {}

    @classmethod
    def from_api(cls, data: Any) -> APIModel:
        """Construct an instance from an API dict, tolerating unknown fields."""
        if not is_dataclass(cls):
            raise TypeError(f"{cls.__name__} must be a dataclass to use APIModel")

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise TypeError(f"{cls.__name__}.from_api expected dict, got {type(data).__name__}")

        aliases = cls._api_aliases
        kwargs: dict[str, Any] = {}
        for field in fields(cls):
            if field.name.startswith("_"):
                continue
            api_key = aliases.get(field.name, field.name)
            if api_key in data:
                kwargs[field.name] = data[api_key]
            elif field.name in data:
                kwargs[field.name] = data[field.name]
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        if not is_dataclass(self):
            raise TypeError(f"{type(self).__name__} must be a dataclass to use APIModel")
        raw = asdict(self)
        return {key: value for key, value in raw.items() if not key.startswith("_")}

# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

import dataclasses
import datetime
import sys
from collections.abc import Mapping, Sequence as CollectionSequence
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    ClassVar,
    ForwardRef,
    Literal,
    Optional,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from marimo._utils.case import to_camel_case

if sys.version_info < (3, 11):
    from typing_extensions import NotRequired
else:
    from typing import NotRequired

from typing import Sequence  # noqa: UP035


class PythonTypeToOpenAPI:
    def __init__(self, name_overrides: dict[Any, str], camel_case: bool):
        self.name_overrides = name_overrides
        self.camel_case = camel_case
        self.optional_name_overrides = {
            Optional[arg]: name for arg, name in name_overrides.items()
        }

    def convert(
        self,
        py_type: Any,
        processed_classes: dict[Any, str],
    ) -> dict[str, Any]:
        """
        Convert a Python type to an OpenAPI schema.

        Returns:
            Dict[str, Any]: The OpenAPI schema.
        """
        origin = get_origin(py_type)
        optional_name_overrides = self.optional_name_overrides

        if isinstance(py_type, ForwardRef):
            return {"$ref": f"#/components/schemas/{py_type.__forward_arg__}"}

        # Handle NewType by unwrapping to its base type
        if hasattr(py_type, "__supertype__"):  # NewType check
            return self.convert(py_type.__supertype__, processed_classes)

        if origin is Union:
            args = get_args(py_type)
            if py_type in processed_classes:
                ref = processed_classes[py_type]
                return {"$ref": f"#/components/schemas/{ref}"}
            if py_type in optional_name_overrides:
                ref = optional_name_overrides[py_type]
                return {
                    "$ref": f"#/components/schemas/{ref}",
                    "nullable": True,
                }

            # Optional is a Union[None, ...]
            if type(None) in args:
                non_none_args = [arg for arg in args if arg is not type(None)]
                if len(non_none_args) == 1:
                    return {
                        **self.convert(
                            non_none_args[0],
                            processed_classes,
                        ),
                        "nullable": True,
                    }
                else:
                    return {
                        "oneOf": _unique(
                            [
                                self.convert(
                                    arg,
                                    processed_classes,
                                )
                                for arg in non_none_args
                            ]
                        ),
                        "nullable": True,
                    }
            else:
                return {
                    "oneOf": _unique(
                        [
                            self.convert(
                                arg,
                                processed_classes,
                            )
                            for arg in args
                        ]
                    )
                }
        elif (
            origin is list
            or origin is CollectionSequence
            or origin is Sequence
        ):
            if py_type in processed_classes:
                ref = processed_classes[py_type]
                return {"$ref": f"#/components/schemas/{ref}"}
            (item_type,) = get_args(py_type)
            return {
                "type": "array",
                "items": self.convert(item_type, processed_classes),
            }
        elif origin is dict or origin is Mapping:
            if py_type in processed_classes:
                ref = processed_classes[py_type]
                return {"$ref": f"#/components/schemas/{ref}"}
            _key_type, value_type = get_args(py_type)
            return {
                "type": "object",
                "additionalProperties": self.convert(
                    value_type, processed_classes
                ),
            }
        elif origin is Literal:
            if py_type in processed_classes:
                ref = processed_classes[py_type]
                return {"$ref": f"#/components/schemas/{ref}"}
            return {"enum": list(get_args(py_type)), "type": "string"}
        elif origin is NotRequired:
            return self.convert(
                get_args(py_type)[0],
                processed_classes,
            )
        elif origin is tuple:
            args = get_args(py_type)
            if len(args) == 2 and isinstance(args[1], type(Ellipsis)):
                return {
                    "type": "array",
                    "items": self.convert(args[0], processed_classes),
                }
            else:
                return {
                    "type": "array",
                    "prefixItems": [
                        self.convert(arg, processed_classes) for arg in args
                    ],
                }
        elif is_typeddict_subclass(py_type):
            if py_type in processed_classes:
                ref = processed_classes[py_type]
                return {"$ref": f"#/components/schemas/{ref}"}

            properties: dict[str, Any] = {}
            required: list[str] = []
            annotations = py_type.__annotations__
            for key, value in get_type_hints(py_type).items():
                properties[to_camel_case(key) if self.camel_case else key] = (
                    self.convert(value, processed_classes)
                )
                annotation = annotations[key]
                if "NotRequired[" not in str(annotation):
                    required.append(
                        to_camel_case(key) if self.camel_case else key
                    )

            # Optional keys come from TypedDict(total=False)
            optional_keys = py_type.__optional_keys__
            # Remove any keys that are optional
            required = [key for key in required if key not in optional_keys]

            schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                schema["required"] = required

            schema_name = self.name_overrides.get(py_type, py_type.__name__)
            processed_classes[py_type] = schema_name

            return schema
        elif dataclasses.is_dataclass(py_type):
            return self.convert_dataclass(py_type, processed_classes)
        elif py_type is Any:
            return {}
        elif py_type is object:
            return {"type": "object", "additionalProperties": True}
        elif py_type is str:
            return {"type": "string"}
        elif py_type is int:
            return {"type": "integer"}
        elif py_type is float:
            return {"type": "number"}
        elif py_type is bool:
            return {"type": "boolean"}
        elif py_type is Decimal:
            return {"type": "number"}
        elif py_type is bytes:
            return {"type": "string", "format": "byte"}
        elif py_type is datetime.date:
            return {"type": "string", "format": "date"}
        elif py_type is datetime.time:
            return {"type": "string", "format": "time"}
        elif py_type is datetime.datetime:
            return {"type": "string", "format": "date-time"}
        elif py_type is datetime.timedelta:
            return {"type": "string", "format": "duration"}
        elif py_type is None:
            return {"type": "null"}
        elif isinstance(py_type, type) and issubclass(py_type, Enum):
            if py_type in processed_classes:
                ref = processed_classes[py_type]
                return {"$ref": f"#/components/schemas/{ref}"}
            return {"type": "string", "enum": [e.value for e in py_type]}
        else:
            raise ValueError(
                f"Unsupported type: py_type={py_type}, origin={origin}"
            )

    def convert_dataclass(
        self,
        cls: Any,
        processed_classes: dict[Any, str],
    ) -> dict[str, Any]:
        """Convert a dataclass to an OpenAPI schema.

        Args:
            cls (Any): The dataclass type or instance to convert.
            processed_classes (Dict[Any, str]): A dictionary of processed classes.

        Raises:
            ValueError: If cls is not a dataclass.

        Returns:
            Dict[str, Any]: The OpenAPI schema.
        """
        # If cls is an instance, get its class
        if not isinstance(cls, type) and dataclasses.is_dataclass(cls):
            cls = cls.__class__

        if not dataclasses.is_dataclass(cls):
            raise ValueError(f"{cls} is not a dataclass")

        if cls in processed_classes:
            return {"$ref": f"#/components/schemas/{processed_classes[cls]}"}

        # Handle both class and instance for __name__ access
        if isinstance(cls, type):
            schema_name = self.name_overrides.get(cls, cls.__name__)
        else:
            schema_name = self.name_overrides.get(cls, cls.__class__.__name__)
        processed_classes[cls] = schema_name

        type_hints = get_type_hints(cls)
        fields: tuple[dataclasses.Field[Any], ...] = dataclasses.fields(cls)
        properties: dict[str, dict[str, Any]] = {}
        required: list[str] = []

        for field in fields:
            cased_field_name = (
                to_camel_case(field.name) if self.camel_case else field.name
            )
            field_type = type_hints[field.name]
            properties[cased_field_name] = self.convert(
                field_type, processed_classes
            )
            if not _is_optional(field_type):
                required.append(cased_field_name)

        # Handle ClassVar that might be initialized already
        for field_name, type_hint in type_hints.items():
            cased_field_name = (
                to_camel_case(field_name) if self.camel_case else field_name
            )
            if get_origin(type_hint) is ClassVar:
                # Literal type
                value = getattr(cls, field_name)
                properties[cased_field_name] = {
                    "type": "string",
                    "enum": [value] if isinstance(value, str) else value,
                }
                required.append(cased_field_name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required

        return schema


def _unique(items: list[Any]) -> list[Any]:
    # Unique dictionaries
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
        result.append(item)
    return result


def _is_optional(field: dataclasses.Field[Any]) -> bool:
    """
    Check if a field is Optional
    """
    return (get_origin(field) is Union and type(None) in get_args(field)) or (
        get_origin(field) is NotRequired
    )


def is_typeddict_subclass(cls: Any) -> bool:
    return (
        isinstance(cls, type)
        and issubclass(cls, dict)
        and hasattr(cls, "__annotations__")
        and hasattr(cls, "__total__")
        and isinstance(cls.__total__, bool)  # ignore
    )

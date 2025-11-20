"""Utility helpers for configurable spiders."""

from __future__ import annotations

import re
from collections.abc import Iterable as IterableABC
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)
from urllib.parse import urljoin as urljoin_href

from scrapy.http import Response
from w3lib.html import remove_tags

T = TypeVar("T", bound=Union[str, Any])


class FieldUtilities:
    """Collection of reusable utilities for field post-processing."""

    # ------------------------------------------------------------------
    # High-level pipeline handlers
    # ------------------------------------------------------------------
    def process_detail(
        self,
        value: Any,
        *,
        key: str,
        rule: Optional[Dict[str, Any]] = None,
        position: str = "suffix",
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        pipeline = self.resolve_detail_pipeline(key, rule, position=position)
        return self.apply_pipeline(value, pipeline, context=context)

    def process_listing(
        self,
        value: Any,
        *,
        key: str,
        rule: Optional[Dict[str, Any]] = None,
        position: str = "suffix",
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        pipeline = self.resolve_listing_pipeline(key, rule, position=position)
        return self.apply_pipeline(value, pipeline, context=context)

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------
    def apply_pipeline(
        self,
        value: Any,
        pipeline: Sequence[str],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not pipeline:
            return value
        return self.run_pipeline(value, pipeline, context=context)

    def run_pipeline(
        self,
        value: Any,
        utilities: Sequence[str],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run the utilities specified by ``utilities`` on ``value`` in order."""
        if not utilities:
            return value

        context = context or {}
        result = value
        for name in utilities:
            handler = getattr(self, name, None)
            if handler is None:
                raise KeyError(f"Unknown utility '{name}'")
            result = handler(result, **context)
        return result

    # ------------------------------------------------------------------
    # Pipeline resolution helpers
    # ------------------------------------------------------------------
    def resolve_detail_pipeline(
        self,
        key: str,
        rule: Optional[Dict[str, Any]],
        *,
        position: str = "suffix",
    ) -> Sequence[str]:
        required = self.required_utilities_for_field(key, rule)
        return self._compose_pipeline(rule, required=required, position=position)

    def resolve_listing_pipeline(
        self,
        key: str,
        rule: Optional[Dict[str, Any]],
        *,
        position: str = "suffix",
    ) -> Sequence[str]:
        required = self.required_utilities_for_field(key, rule)
        return self._compose_pipeline(rule, required=required, position=position)

    def _compose_pipeline(
        self,
        rule: Optional[Dict[str, Any]],
        *,
        required: Iterable[str] = (),
        position: str = "suffix",
    ) -> Sequence[str]:
        declared = self._declared_utilities(rule)
        required_list = list(required or ())
        if not required_list:
            return declared

        if position == "prefix":
            pipeline: List[str] = []
            pipeline.extend(required_list)
            pipeline.extend(name for name in declared if name not in required_list)
            return pipeline

        pipeline = [name for name in declared if name not in required_list]
        pipeline.extend(required_list)
        return pipeline

    def _declared_utilities(self, rule: Optional[Dict[str, Any]]) -> List[str]:
        if isinstance(rule, dict):
            declared = rule.get("utilities") or []
            if isinstance(declared, (list, tuple)):
                return [str(name) for name in declared]
            return [str(declared)]
        return []

    def required_utilities_for_field(
        self, key: Optional[str], rule: Optional[Dict[str, Any]]
    ) -> Tuple[str, ...]:
        if key == "images":
            return ("clean_sequence", "normalize_images")
        if key == "description":
            return ("normalize_description",)
        if key == "price":
            return ("normalize_price",)
        if key == "currency":
            return ("normalize_currency",)
        if isinstance(rule, dict) and rule.get("get_all") is True:
            return ("clean_sequence",)
        return ("clean_value",)

    # ------------------------------------------------------------------
    # Cleaning helpers
    # ------------------------------------------------------------------
    def clean_value(self, value: Optional[Any], **_: Any) -> Optional[Any]:
        if value is None:
            return None
        if isinstance(value, str):
            text = remove_tags(value)
            text = re.sub(r"\s+", " ", text)
            text = text.strip()
            return text or None
        return value

    def clean_sequence(
            self, values: Optional[Any], **context: Any
    ) -> List[str]:
        """Normalize ANY input (string, number, iterable) into a cleaned list of strings."""

        if values is None:
            return []

        # If it's already a primitive → wrap as list
        if isinstance(values, (int, float, bool)):
            iterable = [str(values)]

        # If it's a single string → wrap as list
        elif isinstance(values, str):
            iterable = [values]

        # If it's an iterable (list, tuple, set) → convert to list
        elif isinstance(values, IterableABC) and not isinstance(values, (bytes, bytearray)):
            iterable = list(values)

        # Anything else → wrap as list (e.g. dict, object)
        else:
            iterable = [str(values)]

        cleaned_list: List[str] = []
        for value in iterable:
            cleaned = self.clean_value(value, **context)
            if cleaned is not None:
                cleaned_list.append(cleaned)
        return cleaned_list

    def property_type_normalizer(self, value: Optional[Any], **_: Any) -> Optional[str]:
        if not value:
            return None

        types = {
            "studio": "Studio",
            "apartment": "Apartment",
            "villa": "Villa",
            "townhouse": "Townhouse",
        }

        for key, label in types.items():
            if key in value.lower():
                return label

        return "Other"

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------
    def normalize_images(
        self,
        values: Any,
        *,
        response: Optional[Response] = None,
        base: Optional[Union[Response, str]] = None,
        **_: Any,
    ) -> List[str]:
        if values is None:
            return []

        if isinstance(values, str):
            raw_list = [values]
        elif isinstance(values, IterableABC) and not isinstance(
            values, (bytes, bytearray)
        ):
            raw_list = list(values)
        else:
            raw_list = [values]

        base_ref: Union[Response, str, None] = base or response
        if isinstance(base_ref, Response):
            join_url = base_ref.urljoin
        else:
            base_url = str(base_ref or "")

            def join_url(url: str) -> str:
                return urljoin_href(base_url, url)

        images: List[str] = []
        for raw in raw_list:
            if not isinstance(raw, str):
                continue
            url = raw.strip()
            if not url or url.startswith("data:image/"):
                continue
            images.append(join_url(url))
        return images

    def normalize_description(self, value: Any, **_: Any) -> Optional[str]:
        if value is None:
            return None

        if isinstance(value, (list, tuple, set)):
            parts = [str(v) for v in value if v]
            text = "\n".join(parts)
        else:
            text = str(value)

        text = remove_tags(text)
        text = text.replace("\u00a0", " ")
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned or None

    def normalize_price(self, value: Any, **_: Any) -> Optional[int]:
        if value is None:
            return None

        candidates: Iterable[Any] = (
            value if isinstance(value, (list, tuple, set)) else (value,)
        )

        for candidate in candidates:
            if candidate is None:
                continue
            price_text = str(candidate).strip().replace("\u00a0", " ")
            normalized = self._price_digits(price_text)
            if normalized is not None:
                return normalized
        return None

    def normalize_currency(self, value: Any, **_: Any) -> Optional[str]:
        if value is None:
            return None

        candidates: Iterable[Any] = (
            value if isinstance(value, (list, tuple, set)) else (value,)
        )

        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            currency_text = candidate.strip()
            if not currency_text:
                continue
            if re.search(r"\d+", currency_text) or (2 <= len(currency_text) <= 3):
                match = re.search(r"([A-Za-z]+)", currency_text)
                if match:
                    return match.group(1)
            else:
                return currency_text
        return None

    def absolute_value(self, value: Any, **_: Any) -> Optional[Any]:
        """Return the absolute numeric value of int/float or numeric strings."""
        if value is None:
            return None

        # If it's already a number
        if isinstance(value, (int, float)):
            return abs(value)

        # If it's a string that *might* be a number
        if isinstance(value, str):
            value = value.strip()
            # Try to parse as integer
            if re.fullmatch(r"-?\d+", value):
                return abs(int(value))
            # Try to parse as float
            if re.fullmatch(r"-?\d+(\.\d+)?", value):
                return abs(float(value))

        # Default: return unchanged
        return value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _price_digits(self, price_text: str) -> Optional[int]:
        match = re.search(r"(\d[\d\s,\-/]*)(?:[.,]\d{1,2})?", price_text)
        if not match:
            return None
        normalized = re.sub(r"[\s,\-/]", "", match.group(1))
        if normalized.isdigit():
            return int(normalized)
        return None

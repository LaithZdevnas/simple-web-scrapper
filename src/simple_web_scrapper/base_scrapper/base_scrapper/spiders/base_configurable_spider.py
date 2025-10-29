import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set, Union
from urllib.parse import urljoin as urljoin_href

import scrapy
from scrapy.http import Response
from scrapy.selector import Selector
from scrapy_selenium import SeleniumRequest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from ..items import BaseScrapperItem


class ConfigurableBaseSpider(scrapy.Spider):
    """Shared logic for the configurable spiders.

    The subclasses only need to provide a concrete ``item_cls`` and, when
    necessary, override the hook methods to tweak parsing or pagination
    behaviour.  The intent is to keep the flow exactly the same while making
    the logic easier to reason about and trace through logging.
    """

    item_cls = BaseScrapperItem
    default_wait_time = 10
    pagination_dont_filter = False

    def __init__(
        self, site: Optional[str] = None, config: Optional[str] = None, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        if not site or not config:
            raise ValueError(
                "Pass -a site=<key> and -a config=<path_to_json> (no hardcoded selectors)."
            )

        cfg_path = self._resolve_config_path(config)
        with open(cfg_path, "r", encoding="utf-8") as f:
            sites = json.load(f)
        self.logger.info("Using config: %s", cfg_path)

        self.cfg = sites.get(site)
        if not self.cfg:
            raise ValueError(
                "Site '%s' not found in %s. Available: %s",
                site,
                cfg_path,
                ", ".join(sites.keys()),
            )

        # Everything comes from JSON
        self.allowed_domains = self.cfg["allowed_domains"]
        self.start_url = self.cfg["start_url"]
        self.listing = self.cfg["listing"]
        self.detail = self.cfg["detail"]

    # ------------------------------------------------------------------
    # Core request flow
    # ------------------------------------------------------------------
    def start_requests(self):
        wait_css = self.listing["wait_css"]
        self.logger.info("Starting crawl at %s", self.start_url)
        yield SeleniumRequest(
            url=self.start_url,
            callback=self.parse,
            wait_time=self.default_wait_time,
            wait_until=EC.presence_of_all_elements_located((By.CSS_SELECTOR, wait_css)),
        )

    def parse(self, response: Response, page_num: int = 1):  # type: ignore[override]
        cards = list(self.get_listing_cards(response))
        self.log_listing_summary(response, len(cards), page_num)

        for card in cards:
            title = self.extract_card_title(card)
            href = self.extract_card_href(card)
            listing_fields = self.extract_card_listing_fields(response, card)
            if href:
                request = self.build_detail_request(
                    response, href, title, listing_fields
                )
                self.logger.debug("Queueing detail request for %s", request.url)
                yield request
            else:
                self.logger.debug("Skipping card with missing href on %s", response.url)

        yield from self.handle_pagination(response, page_num)

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------
    def get_listing_cards(self, response: Response) -> Iterable[Selector]:
        cards_selector = self.listing["cards"]
        cards = self._sel_nodes(response, cards_selector)
        self.logger.debug("Extracted %d listing cards", len(cards))
        return cards

    def log_listing_summary(
        self, response: Response, card_count: int, page_num: int
    ) -> None:
        self.logger.info(
            "Found %d cards on %s (page %d)", card_count, response.url, page_num
        )

    def extract_card_title(self, card) -> str:
        rule = self.listing.get("title")
        title = (self._get_one(card, rule) or "").strip()
        self.logger.debug("Extracted title '%s'", title)
        return title

    def extract_card_href(self, card) -> Optional[str]:
        rule = self.listing.get("detail_link")
        href = self._get_one(card, rule)
        if href:
            href = href.strip()
        return href

    def build_detail_request(
        self,
        response: Response,
        href: str,
        title: str,
        listing_fields: Optional[Dict[str, Any]] = None,
    ) -> SeleniumRequest:
        wait_css = self.detail["wait_css"]
        request_kwargs: Dict = {
            "url": response.urljoin(href),
            "callback": self.parse_detail,
            "wait_time": self.default_wait_time,
            "wait_until": EC.presence_of_element_located((By.CSS_SELECTOR, wait_css)),
        }
        cb_kwargs: Dict[str, Any] = {"title": title}
        if listing_fields:
            cb_kwargs["listing_fields"] = listing_fields
        request_kwargs["cb_kwargs"] = cb_kwargs
        return SeleniumRequest(**request_kwargs)

    # ------------------------------------------------------------------
    # Pagination helpers
    # ------------------------------------------------------------------
    def handle_pagination(
        self, response: Response, page_num: int
    ) -> Iterable[SeleniumRequest]:
        btn_rule = self.listing.get("next_button")
        anc_rule = self.listing.get("next_anchor")

        if btn_rule:
            has_button = self._get_one(response, btn_rule)
            if has_button and "css" in btn_rule and btn_rule["css"]:
                request = self.build_next_button_request(response, page_num, btn_rule)
                if request:
                    yield request
                    return  # Buttons take precedence
                else:
                    self.logger.debug("Next button present but no request was built")

        if anc_rule:
            request = self.build_next_anchor_request(response, page_num, anc_rule)
            if request:
                yield request

    def build_next_button_request(
        self, response: Response, page_num: int, btn_rule: Dict
    ) -> Optional[SeleniumRequest]:
        first_card_link_css = self.listing.get("first_card_link_css")
        button_css = btn_rule.get("css")
        if not first_card_link_css or not button_css:
            self.logger.debug("Missing selectors for button pagination")
            return None

        script = self.get_next_button_script(button_css, first_card_link_css)
        if not script:
            self.logger.debug("No pagination script provided")
            return None

        next_page_num = page_num + 1
        cb_kwargs = self.get_pagination_cb_kwargs(next_page_num)
        self.logger.info("Clicking next button to reach page %d", next_page_num)

        request_kwargs: Dict = {
            "url": response.url,
            "callback": self.parse,
            "script": script,
            "wait_time": self.default_wait_time,
            "wait_until": EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, self.listing["wait_css"])
            ),
        }
        if cb_kwargs:
            request_kwargs["cb_kwargs"] = cb_kwargs
        if self.pagination_dont_filter:
            request_kwargs["dont_filter"] = True
        return SeleniumRequest(**request_kwargs)

    def build_next_anchor_request(
        self, response: Response, page_num: int, anc_rule: Dict
    ) -> Optional[SeleniumRequest]:
        next_href = self._get_one(response, anc_rule)
        if not next_href:
            self.logger.debug("Next anchor not found on %s", response.url)
            return None

        next_page_num = page_num + 1
        cb_kwargs = self.get_pagination_cb_kwargs(next_page_num)
        self.logger.info(
            "Navigating to page %d via anchor %s", next_page_num, next_href
        )

        request_kwargs: Dict = {
            "url": next_href,
            "callback": self.parse,
            "wait_time": self.default_wait_time,
            "wait_until": EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, self.listing["wait_css"])
            ),
        }
        if cb_kwargs:
            request_kwargs["cb_kwargs"] = cb_kwargs
        return SeleniumRequest(**request_kwargs)

    def get_next_button_script(self, button_css: str, first_card_link_css: str) -> str:
        button_css_js = json.dumps(button_css)
        first_css_js = json.dumps(first_card_link_css)
        return f"""
            const firstCardBefore = document.querySelector({first_css_js});
            const hrefBefore = firstCardBefore ? firstCardBefore.href : null;
            const button = document.querySelector({button_css_js});
            if (!button) {{
                return false;
            }}
            button.click();
            return new Promise((resolve) => {{
                let attempts = 0;
                const maxAttempts = 50;
                const iv = setInterval(() => {{
                    attempts++;
                    const firstCardNow = document.querySelector({first_css_js});
                    const hrefNow = firstCardNow ? firstCardNow.href : null;
                    if (hrefNow && hrefNow !== hrefBefore) {{
                        clearInterval(iv); resolve(true);
                    }} else if (attempts >= maxAttempts) {{
                        clearInterval(iv); resolve(false);
                    }}
                }}, 100);
            }});
        """

    def get_pagination_cb_kwargs(self, next_page_num: int) -> Optional[Dict]:
        return None

    # ------------------------------------------------------------------
    # Detail helpers
    # ------------------------------------------------------------------
    def parse_detail(
        self,
        response: Response,
        title: str,
        listing_fields: Optional[Dict[str, Any]] = None,
    ):
        item = self.item_cls()
        item["title"] = title
        item["url"] = response.url

        if listing_fields:
            self.populate_listing_fields(response, item, listing_fields)

        fields = self.detail.get("fields", {})
        self.logger.debug("Parsing detail page for %s", response.url)

        self.populate_generic_fields(response, item, fields)
        self.populate_images(response, item, fields)
        self.populate_description(response, item, fields)
        self.populate_price(response, item, fields)
        self.populate_currency(response, item, fields)
        self.populate_additional_detail(response, item, fields)

        yield item

    def populate_generic_fields(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        reserved = self.get_reserved_detail_keys()
        for key, rule in fields.items():
            if key in reserved:
                continue

            if isinstance(rule, dict) and "default_value" in rule:
                cleaned = self._clean_extracted_value(rule["default_value"])
                if cleaned is not None:
                    item[key] = cleaned
                    self.logger.debug(
                        "Field %s assigned default value %s", key, rule["default_value"]
                    )
                continue

            val = self._get_one(response, rule)
            cleaned = self._clean_extracted_value(val)
            if cleaned is not None:
                item[key] = cleaned
                self.logger.debug("Field %s extracted as %s", key, item[key])

    def populate_images(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        images_rule = fields.get("images")
        if not images_rule:
            return

        raw_urls = self._get_all(response, images_rule)
        images = self._normalize_image_values(response, raw_urls)
        if images:
            item["images"] = images
            self.logger.debug("Collected %d images", len(images))

    def populate_description(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        rule = fields.get("description")
        if not rule:
            return

        if isinstance(rule, dict) and rule.get("default_value") is not None:
            description = self._normalize_description_value(rule["default_value"])
            if description:
                item["description"] = description
                self.logger.debug("Description assigned default value")
            return

        if rule.get("get_all") is True:
            parts = self._get_all(response, rule) or []
            if isinstance(parts, str):
                parts = [parts]
            html_blob = "\n".join(p for p in parts if p)
            text = remove_tags(html_blob)
        else:
            html = self._get_one(response, rule)
            text = remove_tags(html) if html else ""

        description = self._normalize_description_value(text)
        if description:
            item["description"] = description
            self.logger.debug(
                "Description populated with %d characters",
                len(item.get("description", "")),
            )

    def populate_price(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        price_rule = fields.get("price")
        if not price_rule:
            return

        if isinstance(price_rule, dict) and "default_value" in price_rule:
            normalized = self._normalize_price_value(price_rule["default_value"])
            if normalized is not None:
                item["price"] = normalized
                self.logger.debug(
                    "Price assigned default value %s", price_rule["default_value"]
                )
            return

        price = self._get_one(response, price_rule)
        normalized = self._normalize_price_value(price)
        if normalized is not None:
            item["price"] = normalized
            self.logger.debug("Price normalised to %s", normalized)

    def normalize_price_digits(self, price_text: str) -> Optional[int]:
        match = re.search(r"(\d[\d\s,\-/]*)(?:[.,]\d{1,2})?", price_text)
        if not match:
            return None
        normalized = re.sub(r"[\s,\-/]", "", match.group(1))
        if normalized.isdigit():
            return int(normalized)
        return None

    def populate_currency(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        currency_rule = fields.get("currency")
        if not currency_rule:
            return

        if isinstance(currency_rule, dict) and "default_value" in currency_rule:
            currency = self._normalize_currency_value(currency_rule["default_value"])
            if currency:
                item["currency"] = currency
                self.logger.debug(
                    "Currency assigned default value %s", currency_rule["default_value"]
                )
            return

        currency = self._get_one(response, currency_rule)
        currency = self._normalize_currency_value(currency)
        if currency:
            item["currency"] = currency
            self.logger.debug("Currency extracted as %s", item.get("currency"))

    def populate_additional_detail(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        """Hook for subclasses to enrich the item."""

    def populate_listing_fields(
        self,
        response: Response,
        item: scrapy.Item,
        listing_fields: Dict[str, Any],
    ) -> None:
        reserved = self.get_reserved_detail_keys()

        base_url = listing_fields.pop("_listing_base", response.url)

        if "images" in listing_fields:
            images = self._normalize_image_values(base_url, listing_fields["images"])
            if images:
                item["images"] = images
                self.logger.debug(
                    "Listing images pre-populated with %d entries", len(images)
                )

        if "description" in listing_fields:
            description = self._normalize_description_value(
                listing_fields.get("description")
            )
            if description:
                item["description"] = description
                self.logger.debug(
                    "Listing description pre-populated with %d characters",
                    len(description),
                )

        if "price" in listing_fields:
            price = self._normalize_price_value(listing_fields.get("price"))
            if price is not None:
                item["price"] = price
                self.logger.debug(
                    "Listing price pre-populated as %s", item.get("price")
                )

        if "currency" in listing_fields:
            currency = self._normalize_currency_value(listing_fields.get("currency"))
            if currency:
                item["currency"] = currency
                self.logger.debug(
                    "Listing currency pre-populated as %s", item.get("currency")
                )

        for key, value in listing_fields.items():
            if key in reserved:
                continue

            cleaned = self._clean_extracted_value(value)
            if cleaned is not None:
                item[key] = cleaned
                self.logger.debug(
                    "Listing field %s pre-populated as %s", key, item[key]
                )

    # ------------------------------------------------------------------
    # Selector utilities
    # ------------------------------------------------------------------
    def _sel_nodes(self, root, rule: Optional[Dict]):
        if not rule:
            return root.css(".__never__")
        if "css" in rule and rule["css"]:
            return root.css(rule["css"])
        if "xpath" in rule and rule["xpath"]:
            return root.xpath(rule["xpath"])
        return root.css(".__never__")

    def _get_one(self, root, rule: Optional[Dict]):
        sel = self._sel_nodes(root, rule)
        return sel.get() or None

    def _get_all(self, root, rule: Optional[Dict]):
        sel = self._sel_nodes(root, rule)
        return sel.getall()

    def get_reserved_detail_keys(self) -> Set[str]:
        return {"images", "description", "price", "currency"}

    def extract_card_listing_fields(self, response: Response, card) -> Dict[str, Any]:
        fields_cfg = self.listing.get("fields", {}) or {}
        listing_data: Dict[str, Any] = {}

        for key, rule in fields_cfg.items():
            value: Any = None

            if isinstance(rule, dict) and "default_value" in rule:
                value = rule["default_value"]
            elif key == "images":
                extracted = self._get_all(card, rule)
                cleaned = self._clean_extracted_sequence(extracted or [])
                value = self._normalize_image_values(response, cleaned)
            elif key == "description":
                if isinstance(rule, dict) and rule.get("get_all") is True:
                    parts = self._get_all(card, rule) or []
                    if isinstance(parts, str):
                        parts = [parts]
                    html_blob = "\n".join(p for p in parts if p)
                    value = self._normalize_description_value(html_blob)
                else:
                    html = self._get_one(card, rule)
                    value = self._normalize_description_value(html)
            elif key == "price":
                candidate = self._get_one(card, rule)
                value = self._normalize_price_value(candidate)
            elif key == "currency":
                candidate = self._get_one(card, rule)
                value = self._normalize_currency_value(candidate)
            elif isinstance(rule, dict) and rule.get("get_all") is True:
                values = self._get_all(card, rule) or []
                cleaned = self._clean_extracted_sequence(values)
                value = cleaned if cleaned else None
            else:
                value = self._get_one(card, rule)
                value = self._clean_extracted_value(value)

            if value is None:
                continue

            if isinstance(value, (list, tuple, set)) and not value:
                continue

            listing_data[key] = value
            self.logger.debug("Listing field %s extracted as %s", key, value)

        if listing_data:
            listing_data["_listing_base"] = response.url

        return listing_data

    def _clean_extracted_value(self, value: Optional[Any]) -> Optional[Any]:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = remove_tags(value).strip()
            return cleaned or None
        return value

    def _clean_extracted_sequence(self, values: Iterable) -> list:
        cleaned_list = []
        for value in values:
            cleaned = self._clean_extracted_value(value)
            if cleaned is not None:
                cleaned_list.append(cleaned)
        return cleaned_list

    def _normalize_image_values(self, base: Union[Response, str], values: Any) -> list:
        if values is None:
            return []

        if isinstance(values, str):
            raw_list = [values]
        elif isinstance(values, (list, tuple, set)):
            raw_list = list(values)
        else:
            raw_list = [values]

        if isinstance(base, Response):
            join_url = base.urljoin
        else:
            base_url = str(base or "")

            def join_url(url: str) -> str:
                return urljoin_href(base_url, url)

        images = []
        for raw in raw_list:
            if not isinstance(raw, str):
                continue
            url = raw.strip()
            if not url:
                continue
            if re.match(r"^data:image/[^;]+;base64,", url or ""):
                continue
            images.append(join_url(url))
        return images

    def _normalize_description_value(self, value: Any) -> Optional[str]:
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

    def _normalize_price_value(self, value: Any) -> Optional[int]:
        if value is None:
            return None

        candidates: Iterable[Any]
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = (value,)

        for candidate in candidates:
            if candidate is None:
                continue
            price_text = str(candidate).strip().replace("\u00a0", " ")
            normalized = self.normalize_price_digits(price_text)
            if normalized is not None:
                return normalized
        return None

    def _normalize_currency_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None

        candidates: Iterable[Any]
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = (value,)

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

    @staticmethod
    def _resolve_config_path(config: str) -> str:
        p = Path(config)
        here = Path(__file__).resolve().parent
        candidates = [
            p,
            here / p,
            here.parent / p,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        tried = " | ".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(f"Config not found. Tried: {tried}")


# ``remove_tags`` is only imported lazily to avoid polluting the module when
# Scrapy loads spiders dynamically.  The helper lives at the bottom to keep the
# top-level namespace tidy.
from w3lib.html import remove_tags  # noqa: E402  # isort:skip

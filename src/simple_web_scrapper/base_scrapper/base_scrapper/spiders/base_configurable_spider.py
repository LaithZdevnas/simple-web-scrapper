import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Set

import scrapy
from scrapy.http import Response
from scrapy.selector import Selector
from scrapy_selenium import SeleniumRequest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..items import BaseScrapperItem
from .field_utilities import FieldUtilities


class ConfigurableBaseSpider(scrapy.Spider):
    item_cls = BaseScrapperItem
    default_wait_time = 30
    pagination_dont_filter = True

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
        self.start_urls = self._resolve_start_urls()
        self.start_url = self.start_urls[0]
        self.listing = self.cfg["listing"]
        self.detail = self.cfg["detail"]
        self.utilities = FieldUtilities()

    # ------------------------------------------------------------------
    # Core request flow
    # ------------------------------------------------------------------
    def start_requests(self):
        wait_until = self._build_wait_condition(self.listing, expect_many=True)
        for start_url in self.start_urls:
            self.logger.info("Starting crawl at %s", start_url)
            yield SeleniumRequest(
                url=start_url,
                callback=self.parse,
                wait_time=self.default_wait_time,
                wait_until=wait_until,
            )

    def parse(self, response, page_num: int = 1):
        driver = response.request.meta["driver"]
        self._ensure_driver_on_response_url(driver, response)
        driver.execute_script("window.scrollBy(0, 1000);")

        sel = Selector(text=driver.page_source)

        cards = list(self.get_listing_cards(sel))  # sel works with your helpers
        self.log_listing_summary(response, len(cards), page_num)

        for card in cards:
            title = self.extract_card_title(card)
            href = self.extract_card_href(card)
            listing_fields = self.extract_card_listing_fields(response, card)
            if href:
                yield self.build_detail_request(response, href, title, listing_fields)

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
        value = self._get_one(card, rule)
        title = self.utilities.process_listing(value, key="title", rule=rule) or ""
        self.logger.debug("Extracted title '%s'", title)
        return title

    def extract_card_href(self, card) -> Optional[str]:
        rule = self.listing.get("detail_link")
        value = self._get_one(card, rule)
        return self.utilities.process_listing(value, key="detail_link", rule=rule)

    def build_detail_request(
        self,
        response: Response,
        href: str,
        title: str,
        listing_fields: Optional[Dict[str, Any]] = None,
    ) -> SeleniumRequest:
        wait_until = self._build_wait_condition(self.detail, expect_many=False)
        request_kwargs: Dict = {
            "url": response.urljoin(href),
            "callback": self.parse_detail,
            "wait_time": self.default_wait_time,
            "wait_until": wait_until,
        }
        cb_kwargs: Dict[str, Any] = {"title": title}
        if listing_fields:
            cb_kwargs["listing_fields"] = listing_fields
        request_kwargs["cb_kwargs"] = cb_kwargs
        return SeleniumRequest(**request_kwargs)

    def _resolve_start_urls(self):
        start_urls = self.cfg.get("start_urls")
        if start_urls:
            if isinstance(start_urls, str):
                return [start_urls]
            if isinstance(start_urls, list) and all(
                isinstance(url, str) for url in start_urls
            ):
                return start_urls
            raise ValueError("'start_urls' must be a string or list of strings")

        legacy_start_url = self.cfg.get("start_url")
        if isinstance(legacy_start_url, str):
            return [legacy_start_url]

        raise ValueError(
            "Config must define 'start_urls' as a string or list of strings."
        )

    def _build_wait_condition(
        self, section_cfg: Dict[str, Any], *, expect_many: bool
    ) -> Callable[[WebDriver], Any]:
        wait_css = section_cfg["wait_css"]
        locator = (By.CSS_SELECTOR, wait_css)
        presence_condition: Callable[[WebDriver], Any]
        if expect_many:
            presence_condition = EC.presence_of_all_elements_located(locator)
        else:
            presence_condition = EC.presence_of_element_located(locator)

        wait_for_absence = section_cfg.get("wait_for_absence")
        if not wait_for_absence:
            return presence_condition

        absence_condition = EC.invisibility_of_element_located(
            (By.CSS_SELECTOR, wait_for_absence)
        )

        def _predicate(driver: WebDriver) -> Any:
            elements = presence_condition(driver)
            if not elements:
                return False
            if not absence_condition(driver):
                return False
            return elements

        return _predicate

    # ------------------------------------------------------------------
    # Pagination helpers
    # ------------------------------------------------------------------
    def handle_pagination(
        self, response: Response, page_num: int
    ) -> Iterable[SeleniumRequest]:
        anc_rule = self.listing.get("next_anchor")

        if anc_rule:
            request = self.build_next_anchor_request(response, page_num, anc_rule)
            if request:
                yield request

    def build_next_anchor_request(
        self, response: Response, page_num: int, anc_rule: Dict
    ) -> Optional[SeleniumRequest]:
        next_href = self._get_one(response, anc_rule)
        if not next_href or next_href.strip() in ("", "#"):
            self.logger.debug(
                "Next anchor not found or invalid (%s) on %s", next_href, response.url
            )
            return None

        next_page_num = page_num + 1
        cb_kwargs = self.get_pagination_cb_kwargs(next_page_num)
        self.logger.info(
            "Navigating to page %d via anchor %s", next_page_num, next_href
        )

        full_url = response.urljoin(next_href)  # handles absolute and relatives URLs
        wait_until = self._build_wait_condition(self.listing, expect_many=True)
        request_kwargs: Dict = {
            "url": full_url,
            "callback": self.parse,
            "wait_time": self.default_wait_time,
            "wait_until": wait_until,
        }
        if cb_kwargs:
            request_kwargs["cb_kwargs"] = cb_kwargs
        return SeleniumRequest(**request_kwargs)

    def _ensure_driver_on_response_url(
        self, driver: WebDriver, response: Response
    ) -> None:
        try:
            current_url = driver.current_url
        except (
            Exception
        ):  # pragma: no cover - defensive guard against unexpected driver issues
            current_url = None

        if self._urls_equivalent(current_url, response.url):
            return

        wait_until = self._build_wait_condition(self.listing, expect_many=True)
        log_current = current_url or "<unavailable>"
        self.logger.debug(
            "Driver URL %s does not match response %s. Reloading page.",
            log_current,
            response.url,
        )

        driver.get(response.url)
        try:
            WebDriverWait(driver, self.default_wait_time).until(wait_until)
        except TimeoutException:
            self.logger.warning(
                "Timed out while waiting for %s to be ready after driver reload.",
                response.url,
            )

    @staticmethod
    def _urls_equivalent(first: Optional[str], second: Optional[str]) -> bool:
        if not first or not second:
            return False
        return first.rstrip("/") == second.rstrip("/")

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
                item[key] = rule["default_value"]
                self.logger.debug(
                    "Field %s assigned default value %s", key, rule["default_value"]
                )
                continue

            if rule.get("get_all"):
                val = self._get_all(response, rule)
                val = " ".join(val).strip() if val else None
            else:
                val = self._get_one(response, rule)

            cleaned = self.utilities.process_detail(
                val, key=key, rule=rule, context={"response": response}
            )
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
        images = self.utilities.process_detail(
            raw_urls,
            key="images",
            rule=images_rule,
            position="prefix",
            context={"response": response},
        )
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
            item["description"] = rule["default_value"]
            self.logger.debug("Description assigned default value")
            return

        if rule.get("get_all") is True:
            raw_value: Any = self._get_all(response, rule) or []
        else:
            raw_value = self._get_one(response, rule)

        description = self.utilities.process_detail(
            raw_value,
            key="description",
            rule=rule,
            context={"response": response},
        )
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
            item["price"] = price_rule["default_value"]
            self.logger.debug(
                "Price assigned default value %s", price_rule["default_value"]
            )
            return

        if price_rule.get("get_all"):
            price = self._get_all(response, price_rule)
            price = " ".join(price).strip() if price else None
        else:
            price = self._get_one(response, price_rule)

        normalized = self.utilities.process_detail(
            price,
            key="price",
            rule=price_rule,
            context={"response": response},
        )
        if normalized is not None:
            item["price"] = normalized
            self.logger.debug("Price normalised to %s", normalized)

    def populate_currency(
        self, response: Response, item: scrapy.Item, fields: Dict
    ) -> None:
        currency_rule = fields.get("currency")
        if not currency_rule:
            return

        if isinstance(currency_rule, dict) and "default_value" in currency_rule:
            item["currency"] = currency_rule["default_value"]
            self.logger.debug(
                "Currency assigned default value %s", currency_rule["default_value"]
            )
            return

        currency = self._get_one(response, currency_rule)
        currency = self.utilities.process_detail(
            currency,
            key="currency",
            rule=currency_rule,
            context={"response": response},
        )
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
        listing_rules = self.listing.get("fields", {}) or {}

        if "images" in listing_fields:
            images = self.utilities.process_listing(
                listing_fields["images"],
                key="images",
                rule=listing_rules.get("images"),
                position="prefix",
                context={"base": base_url, "response": response},
            )
            if images:
                item["images"] = images
                self.logger.debug(
                    "Listing images pre-populated with %d entries", len(images)
                )

        if "description" in listing_fields:
            description = self.utilities.process_listing(
                listing_fields.get("description"),
                key="description",
                rule=listing_rules.get("description"),
                context={"response": response},
            )
            if description:
                item["description"] = description
                self.logger.debug(
                    "Listing description pre-populated with %d characters",
                    len(description),
                )

        if "price" in listing_fields:
            price = self.utilities.process_listing(
                listing_fields.get("price"),
                key="price",
                rule=listing_rules.get("price"),
                context={"response": response},
            )
            if price is not None:
                item["price"] = price
                self.logger.debug(
                    "Listing price pre-populated as %s", item.get("price")
                )

        if "currency" in listing_fields:
            currency = self.utilities.process_listing(
                listing_fields.get("currency"),
                key="currency",
                rule=listing_rules.get("currency"),
                context={"response": response},
            )
            if currency:
                item["currency"] = currency
                self.logger.debug(
                    "Listing currency pre-populated as %s", item.get("currency")
                )

        for key, value in listing_fields.items():
            if key in reserved:
                continue

            cleaned = self.utilities.process_listing(
                value,
                key=key,
                rule=listing_rules.get(key),
                context={"response": response},
            )
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
            expression = rule["xpath"]
            if expression.startswith("//") and isinstance(root, Selector):
                expression = "." + expression
            return root.xpath(expression)
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
                value = self.utilities.process_listing(
                    extracted or [],
                    key=key,
                    rule=rule,
                    position="prefix",
                    context={"response": response},
                )
            elif key == "description":
                if isinstance(rule, dict) and rule.get("get_all") is True:
                    raw_value = self._get_all(card, rule) or []
                else:
                    raw_value = self._get_one(card, rule)
                value = self.utilities.process_listing(
                    raw_value,
                    key=key,
                    rule=rule,
                    context={"response": response},
                )
            elif key == "price":
                candidate = self._get_one(card, rule)
                value = self.utilities.process_listing(
                    candidate,
                    key=key,
                    rule=rule,
                    context={"response": response},
                )
            elif key == "currency":
                candidate = self._get_one(card, rule)
                value = self.utilities.process_listing(
                    candidate,
                    key=key,
                    rule=rule,
                    context={"response": response},
                )
            elif isinstance(rule, dict) and rule.get("get_all") is True:
                values = self._get_all(card, rule) or []
                value = self.utilities.process_listing(
                    values,
                    key=key,
                    rule=rule,
                    position="prefix",
                    context={"response": response},
                )
            else:
                raw_value = self._get_one(card, rule)
                value = self.utilities.process_listing(
                    raw_value,
                    key=key,
                    rule=rule,
                    context={"response": response},
                )

            if value is None:
                continue

            if isinstance(value, (list, tuple, set)) and not value:
                continue

            listing_data[key] = value
            self.logger.debug("Listing field %s extracted as %s", key, value)

        if listing_data:
            listing_data["_listing_base"] = response.url

        return listing_data

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

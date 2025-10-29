import json
import re
from typing import Dict, Optional

from scrapy.http import Response

from .base_configurable_spider import ConfigurableBaseSpider
from ..items import PropertiesScrapperItem
from w3lib.html import remove_tags


class ConfigurablePropertiesSpider(ConfigurableBaseSpider):
    name = "configurable_properties_spider"
    item_cls = PropertiesScrapperItem
    pagination_dont_filter = True

    def log_listing_summary(self, response: Response, card_count: int, page_num: int) -> None:
        self.logger.info("📄 Page %d — Found %d cards on %s", page_num, card_count, response.url)

    def get_pagination_cb_kwargs(self, next_page_num: int):
        return {"page_num": next_page_num}

    def get_reserved_detail_keys(self):
        reserved = super().get_reserved_detail_keys()
        reserved.update({"coordinates", "amenities"})
        return reserved

    # ------------------------------------------------------------------
    # Pagination customisation
    # ------------------------------------------------------------------
    def get_next_button_script(self, button_css: str, first_card_link_css: str) -> str:
        button_css_js = json.dumps(button_css)
        first_css_js = json.dumps(first_card_link_css)
        return f"""
            const firstCardBefore = document.querySelector({first_css_js});
            const button = document.querySelector({button_css_js});
            if (!button || button.disabled) return false;
            button.click();
            return new Promise((resolve) => {{
                let attempts = 0;
                const iv = setInterval(() => {{
                    attempts++;
                    try {{
                        if (!firstCardBefore.isConnected) {{
                            clearInterval(iv); resolve(true);
                        }}
                    }} catch (e) {{
                        clearInterval(iv); resolve(true);
                    }}
                    if (attempts >= 50) {{
                        clearInterval(iv); resolve(false);
                    }}
                }}, 100);
            }});
        """

    # ------------------------------------------------------------------
    # Detail parsing customisation
    # ------------------------------------------------------------------
    def populate_description(self, response: Response, item, fields: Dict) -> None:
        rule = fields.get("description")
        if rule:
            self.populate_rich_text_field(response, item, rule, "description")

        amenities_rule = fields.get("amenities")
        if amenities_rule:
            self.populate_rich_text_field(response, item, amenities_rule, "amenities")

    def populate_rich_text_field(
        self, response: Response, item, rule: Dict, item_key: str
    ) -> None:
        if isinstance(rule, dict) and "default_value" in rule:
            item[item_key] = rule["default_value"]
            self.logger.debug("%s assigned default value", item_key)
            return

        if rule.get("get_all") is True:
            parts = self._get_all(response, rule) or []
            if isinstance(parts, str):
                parts = [parts]
            cleaned_parts = []
            for part in parts:
                if not part:
                    continue
                cleaned = self.sanitize_text(remove_tags(part))
                if cleaned:
                    cleaned_parts.append(cleaned)
            if item_key == "amenities":
                item[item_key] = ", ".join(cleaned_parts)
            else:
                item[item_key] = " ".join(cleaned_parts)
        else:
            html = self._get_one(response, rule)
            if not html:
                return
            text = self.sanitize_text(remove_tags(html))
            if item_key == "amenities":
                text = re.sub(r"\s*[•\|\n\r;/]\s*", ", ", text)
                text = re.sub(r"(,\s*){2,}", ", ", text).strip(", ")
            item[item_key] = text

    def populate_price(self, response: Response, item, fields: Dict) -> None:
        super().populate_price(response, item, fields)
        if "price" in item and isinstance(item["price"], int):
            self.logger.debug("Property price normalised to %s", item["price"])

    def normalize_price_digits(self, price_text: str) -> Optional[int]:
        match = re.search(r"(\d[\d\s,\-/]*)(?:[.,]\d{1,2})?", price_text)
        if not match:
            return None
        normalized = re.sub(r"[\s,\-/]", "", match.group(1))
        if normalized.isdigit():
            return int(normalized)
        return None

    def populate_currency(self, response: Response, item, fields: Dict) -> None:
        super().populate_currency(response, item, fields)
        if "currency" in item:
            self.logger.debug("Currency finalised as %s", item["currency"])

    def populate_additional_detail(self, response: Response, item, fields: Dict) -> None:
        self.populate_coordinates(response, item, fields)

    def populate_coordinates(self, response: Response, item, fields: Dict) -> None:
        coord_rule = fields.get("coordinates")
        if not coord_rule:
            return
        src = self._get_one(response, coord_rule)
        if not src:
            return
        match = re.search(r"([+-]?\d+(?:\.\d+)?),\s*([+-]?\d+(?:\.\d+)?)", str(src))
        if match:
            item["coordinates"] = {
                "lat": float(match.group(1)),
                "lng": float(match.group(2)),
            }
            self.logger.debug("Coordinates extracted as %s", item["coordinates"])

    @staticmethod
    def sanitize_text(text: str) -> str:
        text = text.replace("\u00a0", " ")
        text = text.replace("\ufffd", "")
        text = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

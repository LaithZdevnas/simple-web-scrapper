import json
import re
from pathlib import Path

import scrapy
from scrapy_selenium import SeleniumRequest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from w3lib.html import remove_tags

from ..items import *


class ConfigurablePropertiesSpider(scrapy.Spider):
    name = "configurable_properties_spider"

    def __init__(self, site=None, config=None, *args, **kwargs):

        super().__init__(*args, **kwargs)
        if not site or not config:
            raise ValueError(
                "Pass -a site=<key> and -a config=<path_to_json> (no hardcoded selectors)."
            )

        cfg_path = self._resolve_config_path(config)
        with open(cfg_path, "r", encoding="utf-8") as f:
            sites = json.load(f)
        self.logger.info(f"Using config: {cfg_path}")

        self.cfg = sites.get(site)
        if not self.cfg:
            raise ValueError(
                f"Site '{site}' not found in {cfg_path}. Available: {', '.join(sites.keys())}"
            )

        # Everything comes from JSON
        self.allowed_domains = self.cfg["allowed_domains"]
        self.start_url = self.cfg["start_url"]
        self.listing = self.cfg["listing"]
        self.detail = self.cfg["detail"]

    # ----------------- tiny selector utilities (no defaults) -----------------
    def _sel_nodes(self, root, rule):
        if not rule:
            return root.css(".__never__")
        if "css" in rule and rule["css"]:
            return root.css(rule["css"])
        if "xpath" in rule and rule["xpath"]:
            return root.xpath(rule["xpath"])
        return root.css(".__never__")

    def _get_one(self, root, rule):
        sel = self._sel_nodes(root, rule)
        return sel.get() or None

    def _get_all(self, root, rule):
        sel = self._sel_nodes(root, rule)
        return sel.getall()

    # ----------------------------- flow -----------------------------
    def start_requests(self):
        wait_css = self.listing["wait_css"]
        yield SeleniumRequest(
            url=self.start_url,
            callback=self.parse,
            wait_time=10,
            wait_until=EC.presence_of_all_elements_located((By.CSS_SELECTOR, wait_css)),
        )

    def parse(self, response, page_num=1):
        # cards
        cards = self._sel_nodes(response, self.listing["cards"])
        self.logger.info(
            "📄 Page %d — Found %d cards on %s", page_num, len(cards), response.url
        )

        for card in cards:
            title = (self._get_one(card, self.listing["title"]) or "").strip()
            href = self._get_one(card, self.listing["detail_link"])
            if href:
                yield SeleniumRequest(
                    url=response.urljoin(href),
                    callback=self.parse_detail,
                    cb_kwargs={"title": title},
                    wait_time=10,
                    wait_until=EC.presence_of_element_located(
                        (By.CSS_SELECTOR, self.detail["wait_css"])
                    ),
                )

        # pagination: try button then anchor
        btn_rule = self.listing.get("next_button")  # click JS path (CSS required)
        anc_rule = self.listing.get("next_anchor")  # href path (css/xpath ok)

        btn_present = self._get_one(response, btn_rule) if btn_rule else None

        if btn_present and "css" in btn_rule and btn_rule["css"]:
            first_card_link_css = self.listing.get("first_card_link_css")
            button_css = btn_rule["css"]

            if first_card_link_css:
                button_css_js = json.dumps(button_css)
                first_css_js = json.dumps(first_card_link_css)

                next_page_num = page_num + 1
                self.logger.info(
                    "➡️ Clicking next button to go to page %d...", next_page_num
                )

                yield SeleniumRequest(
                    url=response.url,
                    callback=self.parse,
                    dont_filter=True,
                    cb_kwargs={"page_num": next_page_num},
                    script=f"""
                        const firstCardBefore = document.querySelector({first_css_js});
                        const button = document.querySelector({button_css_js});
                        if (!button || button.disabled) return false;
                        button.click();

                        // Wait for the old element to become stale (detached from DOM)
                        return new Promise((resolve) => {{
                            let attempts = 0;
                            const iv = setInterval(() => {{
                                attempts++;
                                try {{
                                    firstCardBefore.isConnected;
                                    if (!firstCardBefore.isConnected) {{
                                        clearInterval(iv); resolve(true);
                                    }}
                                }} catch(e) {{
                                    clearInterval(iv); resolve(true);
                                }}
                                if (attempts >= 50) {{
                                    clearInterval(iv); resolve(false);
                                }}
                            }}, 100);
                        }});
                    """,
                    wait_time=10,
                    wait_until=EC.presence_of_all_elements_located(
                        (By.CSS_SELECTOR, self.listing["wait_css"])
                    ),
                )

        elif anc_rule:
            next_href = self._get_one(response, anc_rule)
            if next_href:
                next_page_num = page_num + 1
                self.logger.info(
                    "➡️ Navigating to next page %d via anchor: %s",
                    next_page_num,
                    next_href,
                )
                yield SeleniumRequest(
                    url=next_href,
                    callback=self.parse,
                    cb_kwargs={"page_num": next_page_num},
                    wait_time=10,
                    wait_until=EC.presence_of_all_elements_located(
                        (By.CSS_SELECTOR, self.listing["wait_css"])
                    ),
                )

    def parse_detail(self, response, title):
        item = PropertiesScrapperItem()
        item["title"] = title
        item["url"] = response.url
        fields = self.detail.get("fields", {})

        # ---------------- Generic Fields ----------------
        for key, rule in fields.items():
            if key in (
                "images",
                "description",
                "price",
                "currency",
                "coordinates",
                "amenities",
            ):
                continue

            if isinstance(rule, dict) and "default_value" in rule:
                item[key] = rule["default_value"]
                continue

            val = self._get_one(response, rule)
            if val is not None:
                item[key] = val.strip()

        # ---------------- Images ----------------
        if "images" in fields:
            raw_urls = self._get_all(response, fields["images"])
            item["images"] = [
                response.urljoin(u)
                for u in raw_urls
                if not re.match(r"^data:image/[^;]+;base64,", u or "")
            ]

        # ---------------- Coordinates ----------------
        if "coordinates" in fields:
            src = self._get_one(response, fields["coordinates"])
            if src:
                m = re.search(r"([+-]?\d+(?:\.\d+)?),\s*([+-]?\d+(?:\.\d+)?)", str(src))
                if m:
                    item["coordinates"] = {
                        "lat": float(m.group(1)),
                        "lng": float(m.group(2)),
                    }

        # ---------------- Description & Amenities ----------------
        for key, out_key in (
            ("description", "description"),
            ("amenities", "amenities"),
        ):
            if key in fields:
                rule = fields[key]

                if isinstance(rule, dict) and "default_value" in rule:
                    item[out_key] = rule["default_value"]
                    continue

                if rule and rule.get("get_all") is True:
                    parts = self._get_all(response, rule) or []
                    if isinstance(parts, str):
                        parts = [parts]

                    cleaned_parts = []
                    for p in parts:
                        if not p:
                            continue
                        t = self.sanitize_text(remove_tags(p))
                        if t:
                            cleaned_parts.append(t)

                    if out_key == "amenities":
                        item[out_key] = ", ".join(cleaned_parts)
                    else:
                        item[out_key] = " ".join(cleaned_parts)

                else:
                    html = self._get_one(response, rule)
                    if html:
                        text = self.sanitize_text(remove_tags(html))
                        if out_key == "amenities":
                            # Turn bullets/newlines/semicolons into commas
                            text = re.sub(r"\s*[•\|\n\r;/]\s*", ", ", text)
                            text = re.sub(r"(,\s*){2,}", ", ", text).strip(", ")
                        item[out_key] = text

        # ---------------- Price ----------------
        if "price" in fields:
            if isinstance(fields["price"], dict) and "default_value" in fields["price"]:
                item["price"] = fields["price"]["default_value"]
            else:
                price = self._get_one(response, fields["price"])
                if price:
                    price_text = str(price).strip().replace("\u00a0", " ")
                    m = re.search(r"(\d[\d\s,]*)(?:[.,]\d{1,2})?", price_text)
                    if m:
                        normalized = re.sub(r"[\s,]", "", m.group(1))
                        normalized = normalized.replace("-", "").replace("/", "")
                        if normalized.isdigit():
                            item["price"] = int(normalized)

        if "currency" in fields:
            if (
                isinstance(fields["currency"], dict)
                and "default_value" in fields["currency"]
            ):
                item["currency"] = fields["currency"]["default_value"]
            else:
                currency = self._get_one(response, fields["currency"])
                if currency:
                    currency_text = currency.strip()
                    if re.search(r"\d", currency_text) or (
                        re.search(r"[A-Za-z]{2,3}", currency_text)
                        and len(currency_text)
                    ):
                        cur_match = re.search(r"([A-Za-z]+)", currency_text)
                        item["currency"] = cur_match.group(1) if cur_match else None

        yield item

    @staticmethod
    def _resolve_config_path(config: str) -> str:
        p = Path(config)
        here = Path(__file__).resolve().parent  # .../base_scrapper/spiders
        candidates = [
            p,  # as passed (absolute or relative to CWD)
            here / p,  # relative to this file
            here.parent / p,  # relative to package folder (.../base_scrapper)
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
        tried = " | ".join(str(c) for c in candidates)
        raise FileNotFoundError(f"Config not found. Tried: {tried}")

    def sanitize_text(self, s: str) -> str:
        # Normalize spaces, remove U+FFFD and control chars
        s = s.replace("\u00a0", " ")  # NBSP -> space
        s = s.replace("\ufffd", "")  # drop replacement char
        s = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", s)  # control chars
        s = re.sub(r"\s+", " ", s).strip()
        return s

import json
import re
from pathlib import Path

import scrapy
from scrapy_selenium import SeleniumRequest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from w3lib.html import remove_tags

from ..items import *


class ConfigurableCarSpider(scrapy.Spider):
    name = "configurable_car_spider"

    def __init__(self, site=None, config=None, *args, **kwargs):
        """
        Run:
          scrapy crawl configurable_car_spider -a site=pupiloffate -a config=base_scrapper/spiders/configs/sites.json -O out.csv
        """
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

    def parse(self, response):
        # cards
        cards = self._sel_nodes(response, self.listing["cards"])
        self.logger.info("Found %d cards on %s", len(cards), response.url)

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
                # Safely quote selectors for JS using JSON string literals
                button_css_js = json.dumps(button_css)
                first_css_js = json.dumps(first_card_link_css)

                yield SeleniumRequest(
                    url=response.url,
                    callback=self.parse,
                    dont_filter=True,
                    script=f"""
                        // Capture first card BEFORE click
                        const firstCardBefore = document.querySelector({first_css_js});
                        const hrefBefore = firstCardBefore ? firstCardBefore.href : null;

                        // Find and click the next button
                        const button = document.querySelector({button_css_js});
                        if (!button) {{
                            return false; // no next button -> stop paginating
                        }}
                        button.click();

                        // Wait up to ~5s for first card href to change
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
                    """,
                    wait_time=10,
                    # 2) Wait for the LISTING grid, not the detail page
                    wait_until=EC.presence_of_all_elements_located(
                        (By.CSS_SELECTOR, self.listing["wait_css"])
                    ),
                )

        elif anc_rule:
            next_href = self._get_one(response, anc_rule)
            if next_href:
                yield SeleniumRequest(
                    url=next_href,
                    callback=self.parse,
                    wait_time=10,
                    wait_until=EC.presence_of_all_elements_located(
                        (By.CSS_SELECTOR, self.listing["wait_css"])
                    ),
                )

    def parse_detail(self, response, title):
        item = BaseScrapperItem()
        item["title"] = title
        item["url"] = response.url

        fields = self.detail.get("fields", {})

        for key, rule in fields.items():
            if key in ("images", "description", "price", "currency"):
                continue
            val = self._get_one(response, rule)
            if val is not None:
                item[key] = val.strip()

        if "images" in fields:
            raw_urls = self._get_all(response, fields["images"])
            item["images"] = [
                response.urljoin(u)
                for u in raw_urls
                if not re.match(r"^data:image/[^;]+;base64,", u or "")
            ]

        if "description" in fields:
            desc_html = self._get_one(response, fields["description"])
            if desc_html:
                text = remove_tags(desc_html)
                item["description"] = re.sub(r"\s+", " ", text).strip()

        if "doors" in self.detail:
            doors_src = self._get_one(response, self.detail["doors"])
            if doors_src:
                m = re.search(r"(\d+)", doors_src)
                if m:
                    item["doors"] = int(m.group(1))

        if "price" in fields:
            price = self._get_one(response, fields["price"])
            if price:
                price_text = str(price).strip().replace("\u00a0", " ")  # NBSP → space
                m = re.search(r"(\d[\d\s,]*)(?:[.,]\d{1,2})?", price_text)
                if m:
                    normalized = re.sub(
                        r"[\s,]", "", m.group(1)
                    )  # drop spaces & commas
                    if normalized.isdigit():
                        item["price"] = int(normalized)

        if "currency" in fields:
            currency = self._get_one(response, fields["currency"])
            if currency:
                currency_text = currency.strip()
                if re.search(r"\d", currency_text) or (
                    re.search(r"[A-Za-z]{2,3}", currency_text) and len(currency_text)
                ):
                    cur_match = re.search(r"([A-Za-z]+)", currency_text)
                    item["currency"] = cur_match.group(1) if cur_match else None

        # Keep optional schema keys if your pipeline expects them
        # for opt in ["warranty", "regional_specs", "body_type", "color", "location", "seats", "accidents", "vin"]:
        #     item.setdefault(opt, None)

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

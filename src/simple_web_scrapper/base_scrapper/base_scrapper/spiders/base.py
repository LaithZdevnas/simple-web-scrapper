import re

from scrapy_selenium import SeleniumRequest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from w3lib.html import remove_tags

from ..items import *


class BaseSpider(scrapy.Spider):
    name = "base"
    allowed_domains = ["trading.pupiloffate.ae"]

    def start_requests(self):
        url = "https://trading.pupiloffate.ae/search/?car-stock=featured"
        yield SeleniumRequest(
            url=url,
            callback=self.parse,
            wait_time=10,
            wait_until=EC.presence_of_all_elements_located(
                (
                    By.CSS_SELECTOR,
                    "div.vehica-inventory-v1__2-cols > div > div.vehica-inventory-v1__row-grid > div",
                )
            ),
            # Example: you can run JS before giving control back to Scrapy:
            # script="window.scrollTo(0, document.body.scrollHeight);",
        )

    def parse(self, response):
        self.logger.info("A response from %s just arrived!", response.url)

        curr_txt = response.css("span.vehica-pagination-mobile__start::text").get()
        try:
            curr_page = int(curr_txt) if curr_txt else 1
        except ValueError:
            curr_page = 1

        cards = response.css(
            "div.vehica-inventory-v1__2-cols > div > div.vehica-inventory-v1__row-grid > div"
        )
        self.logger.info("Found %s cards on page %s", len(cards), curr_page)

        first_card_href = None
        for card in cards:
            first_card_href = yield from self.handle_detail_url(
                card, first_card_href, response
            )

        next_button = response.css(
            "button.vehica-pagination-mobile__arrow.vehica-pagination-mobile__arrow--right"
            ":not([disabled]):not(.disabled):not([aria-disabled='true'])"
        ).get()

        # Check for anchor-based pagination
        next_anchor = response.css(
            "a.vehica-pagination-mobile__arrow.vehica-pagination-mobile__arrow--right::attr(href)"
        ).get()

        if next_button:
            yield from self.next_button_pager(curr_page, first_card_href, response)
        elif next_anchor:
            yield from self.next_href_pager(curr_page, next_anchor, response)
        else:
            self.logger.info("No more pages found after page %s", curr_page)

    def handle_detail_url(self, card, first_card_href, response):
        title = (
            card.css("div > div > div > div.vehica-car-row__content > span::text")
            .get()
            .strip()
        )
        detail_href = card.css("div > div > a.vehica-car-card-link::attr(href)").get()

        self.logger.info("HREF IS from %s just arrived!", detail_href)

        if not first_card_href and detail_href:
            first_card_href = detail_href
        if detail_href:
            # Pass listing data to the detail parser via cb_kwargs (preferred)
            yield SeleniumRequest(
                url=response.urljoin(detail_href),
                callback=self.parse_detail,
                cb_kwargs={"title": title},
                wait_time=10,
                wait_until=EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "body")  # swap to a stable selector on detail
                ),
            )
        return first_card_href

    def next_href_pager(self, curr_page, next_anchor, response):
        self.logger.info(
            "Found anchor Next link on page %s: %s", curr_page, next_anchor
        )
        yield SeleniumRequest(
            url=response.urljoin(next_anchor),
            callback=self.parse,
            wait_time=10,
            wait_until=EC.presence_of_all_elements_located(
                (
                    By.CSS_SELECTOR,
                    "div.vehica-inventory-v1__2-cols > div > div.vehica-inventory-v1__row-grid > div",
                )
            ),
        )

    def next_button_pager(self, curr_page, first_card_href, response):
        self.logger.info("Found Next BUTTON on page %s", curr_page)
        # Extract slug from first card
        first_card_slug = first_card_href.split("/")[-2] if first_card_href else None
        self.logger.info("Will wait for content to change from: %s", first_card_slug)
        yield SeleniumRequest(
            url=response.url,
            callback=self.parse,
            dont_filter=True,
            meta={"current_page": curr_page},
            script=f"""
                    console.log('=== Starting pagination ===');

                    // Store the first card's href BEFORE clicking
                    const firstCardBefore = document.querySelector('a.vehica-car-card-link');
                    const hrefBefore = firstCardBefore ? firstCardBefore.href : null;
                    console.log('First card before:', hrefBefore);

                    // Click the button
                    const button = document.querySelector('button.vehica-pagination-mobile__arrow.vehica-pagination-mobile__arrow--right:not([disabled])');
                    if (!button) {{
                        console.log('ERROR: Button not found');
                        return;
                    }}

                    button.click();
                    console.log('Button clicked');

                    // Wait for the first card to change (with timeout)
                    return new Promise((resolve) => {{
                        let attempts = 0;
                        const maxAttempts = 50; // 50 attempts * 100ms = 5 seconds max

                        const checkInterval = setInterval(() => {{
                            attempts++;
                            const firstCardNow = document.querySelector('a.vehica-car-card-link');
                            const hrefNow = firstCardNow ? firstCardNow.href : null;

                            // Check if content changed
                            if (hrefNow && hrefNow !== hrefBefore) {{
                                console.log('Content changed! New first card:', hrefNow);
                                clearInterval(checkInterval);
                                resolve(true);
                            }} else if (attempts >= maxAttempts) {{
                                console.log('Timeout waiting for content change');
                                clearInterval(checkInterval);
                                resolve(false);
                            }}
                        }}, 100); // Check every 100ms
                    }});
                """,
            wait_time=15,  # Overall timeout
            wait_until=EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "div.vehica-car-card-row-wrapper.vehica-car")
            ),
        )

    def save_response_test(self, response):
        filename = "respone.txt"
        with open(filename, mode="w", encoding="utf-8") as f:
            f.write(response.text)  # response.text is a Unicode string

    def parse_detail(self, response, title):
        self.save_response_test(response)

        item = BaseScrapperItem()
        item["title"] = title
        item["url"] = response.url
        item["transmission"] = self.get_text(
            response,
            "//div[@class='vehica-grid__element vehica-grid__element--1of1 vehica-grid__element--tablet-1of2 vehica-grid__element--mobile-1of1']//div[@class='vehica-car-attributes__values vehica-grid__element--1of2'][normalize-space()='Automatic' or normalize-space()='Manual']/text()",
            False,
        )
        item["year"] = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Year:")]/following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            False,
        )
        item["mileage"] = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Mileage:")]/following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            False,
        )
        item["brand"] = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Make:")]/following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            False,
        )
        item["model"] = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Model:")]/following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            False,
        )
        item["wheel_drive"] = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Drive Type:")]/following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            False,
        )
        item["condition"] = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Condition:")]/following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            False,
        )
        item["images"] = self.image_urls(response)
        item["warranty"] = self.get_text(response, "")
        item["regional_specs"] = self.get_text(response, "")
        item["body_type"] = self.get_text(response, "")
        item["color"] = self.get_text(response, "")
        item["location"] = self.get_text(response, "")
        item["seats"] = self.get_text(response, "")
        item["accidents"] = self.get_text(response, "")
        item["vin"] = self.get_text(response, "")

        self.assign_doors(item, response)
        self.assign_amount_currency(item, response)
        self.assign_description(item, response)
        yield item

    def image_urls(self, response):
        raw_urls = response.xpath(
            "//div[contains(@class,'vehica-swiper-slide')]//img/@src"
        ).getall()
        # filter out data:image/gif;base64 placeholder images
        clean_urls = [
            response.urljoin(u)
            for u in raw_urls
            if not re.match(r"^data:image/[^;]+;base64,", u)
        ]
        return clean_urls

    def assign_doors(self, item, response):
        # later in your spider:
        doors_text = self.get_text(
            response,
            '//div[contains(@class,"vehica-car-attributes__name") and contains(normalize-space(.),"Doors:")]/'
            'following-sibling::div[contains(@class,"vehica-car-attributes__values")][1]/text()',
            is_css=False,
        )
        if doors_text:
            m = re.search(r"(\d+)", doors_text)
            if m:
                item["doors"] = int(m.group(1))

    def get_text(self, response, selector, is_css=True):
        if selector and len(selector) > 0:
            if is_css:
                raw = response.css(selector).get()
            else:
                raw = response.xpath(selector).get()
            if raw:
                cleaned = raw.strip()
                return cleaned if cleaned else None
        return None

    def assign_description(self, item, response):
        desc_html = response.css("div.vehica-car-description").get()
        if desc_html:
            clean_desc = (
                remove_tags(desc_html).replace("\n", " ").replace("\r", " ").strip()
            )
            item["description"] = re.sub(r"\s+", " ", clean_desc)

    def assign_amount_currency(self, item, response):
        currency_text = response.css("div.vehica-car-price:nth-of-type(1)::text").get()
        if currency_text:
            currency_text = currency_text.strip()
            if re.search(r"\d", currency_text):
                cur_match = re.search(r"([A-Za-z]+)", currency_text)
                item["currency"] = cur_match.group(1) if cur_match else None

        # Extract price
        price_text = response.css("div.vehica-car-price:nth-of-type(1)::text").get()
        if price_text:
            price_text = price_text.strip()
            num_match = re.search(r"([\d,]+)", price_text)
            if num_match:
                item["price"] = int(num_match.group(1).replace(",", ""))

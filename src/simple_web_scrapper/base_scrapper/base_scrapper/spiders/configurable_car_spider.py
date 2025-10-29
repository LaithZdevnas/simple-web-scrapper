import re

from scrapy.http import Response

from .base_configurable_spider import ConfigurableBaseSpider
from ..items import BaseScrapperItem


class ConfigurableCarSpider(ConfigurableBaseSpider):
    name = "configurable_car_spider"
    item_cls = BaseScrapperItem

    def log_listing_summary(self, response: Response, card_count: int, page_num: int) -> None:
        self.logger.info("Found %d cards on %s", card_count, response.url)

    def get_pagination_cb_kwargs(self, next_page_num: int):
        return None

    def populate_additional_detail(self, response: Response, item, fields):
        self.populate_doors(response, item)

    def populate_doors(self, response: Response, item) -> None:
        doors_rule = self.detail.get("doors")
        if not doors_rule:
            return
        doors_src = self._get_one(response, doors_rule)
        if not doors_src:
            return
        match = re.search(r"(\d+)", doors_src)
        if match:
            item["doors"] = int(match.group(1))
            self.logger.debug("Doors extracted as %s", item["doors"])

    def get_next_button_script(self, button_css: str, first_card_link_css: str) -> str:
        # Reuse the default behaviour defined in the base class but keep the
        # method explicit so that it is easy to tweak car-specific pagination
        # in the future.
        return super().get_next_button_script(button_css, first_card_link_css)

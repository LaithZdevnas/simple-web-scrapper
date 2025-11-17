import re

from scrapy.http import Response

from ..items import BaseScrapperItem
from .base_playwright_configurable_spider import PlaywrightConfigurableBaseSpider


class PlaywrightConfigurableCarSpider(PlaywrightConfigurableBaseSpider):
    name = "playwright_configurable_car_spider"
    item_cls = BaseScrapperItem

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

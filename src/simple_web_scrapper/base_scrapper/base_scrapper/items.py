# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class BaseScrapperItem(scrapy.Item):
    url = scrapy.Field()
    title = scrapy.Field()
    price = scrapy.Field()
    currency = scrapy.Field()
    location = scrapy.Field()
    description = scrapy.Field()
    year = scrapy.Field()
    mileage = scrapy.Field()
    warranty = scrapy.Field()
    regional_specs = scrapy.Field()
    transmission = scrapy.Field()
    body_type = scrapy.Field()
    color = scrapy.Field()
    doors = scrapy.Field()
    brand = scrapy.Field()
    model = scrapy.Field()
    seats = scrapy.Field()
    wheel_drive = scrapy.Field()
    accidents = scrapy.Field()
    condition = scrapy.Field()
    vin = scrapy.Field()
    images = scrapy.Field()


class PropertiesScrapperItem(scrapy.Item):
    url = scrapy.Field()
    title = scrapy.Field()
    price = scrapy.Field()
    currency = scrapy.Field()
    description = scrapy.Field()
    rent_or_buy = scrapy.Field()
    bedrooms = scrapy.Field()
    bathrooms = scrapy.Field()
    images = scrapy.Field()
    location = scrapy.Field()
    city = scrapy.Field()
    coordinates = scrapy.Field()
    size = scrapy.Field()
    property_type = scrapy.Field()
    amenities = scrapy.Field()
    year = scrapy.Field()

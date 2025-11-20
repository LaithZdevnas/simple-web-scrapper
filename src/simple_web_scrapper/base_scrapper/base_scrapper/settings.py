
# Scrapy settings for base_scrapper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html
import logging
from shutil import which

from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.remote.remote_connection import LOGGER as SELENIUM_LOGGER
from webdriver_manager.chrome import ChromeDriverManager

BOT_NAME = "base_scrapper"

SPIDER_MODULES = ["base_scrapper.spiders"]
NEWSPIDER_MODULE = "base_scrapper.spiders"

# --- Selenium driver config ---
SELENIUM_DRIVER_NAME = "chrome"
SELENIUM_DRIVER_EXECUTABLE_PATH = which('chromedriver') or '/usr/local/bin/chromedriver'
SELENIUM_BROWSER_EXECUTABLE_PATH = "/usr/bin/google-chrome"

SELENIUM_DRIVER_ARGUMENTS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--remote-debugging-port=9222",
    "--window-size=1920,1080",
    "--disable-background-networking",
    "--no-first-run",
    "--no-default-browser-check"
]

RETRY_ENABLED = True
RETRY_TIMES = 2  # Retry failed requests twice

SELENIUM_LOGGER.setLevel(logging.WARNING)


DOWNLOADER_MIDDLEWARES = {
    "base_scrapper.middlewares.RandomUserAgentMiddleware": 400,
    "scrapy_selenium.SeleniumMiddleware": 800
}

USER_AGENTS_POOL = [
    # Desktop Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15",
    # Desktop Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:117.0) Gecko/20100101 Firefox/117.0",
    # Mobile
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0 Mobile Safari/537.36",
]


# --- Playwright integration ---
# DOWNLOAD_HANDLERS = {
#     "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
#     "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
# }

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# PLAYWRIGHT_BROWSER_TYPE = "chromium"
# PLAYWRIGHT_DEFAULT_NAVIGATION_PAGE_GOTO_OPTIONS = {"wait_until": "networkidle"}
# PLAYWRIGHT_LAUNCH_OPTIONS = {
#     "headless": True,
#     "args": [
#         "--no-sandbox",
#         "--disable-gpu",
#         "--disable-dev-shm-usage",
#         "--disable-extensions",
#         "--disable-software-rasterizer",
#         "--disable-background-networking",
#         "--no-first-run",
#         "--no-default-browser-check",
#         "--window-size=1920,1080",
#     ],
# }
# PLAYWRIGHT_MAX_CONTEXTS = 2
# PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = 4

# def should_abort_request(request):
#     return request.resource_type == "image" or request.url.endswith(".webp")

# PLAYWRIGHT_ABORT_REQUEST = should_abort_request

# Crawl responsibly by identifying yourself (and your website) on the user-agent
# USER_AGENT = "base_scrapper (+http://www.yourdomain.com)"

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

# Concurrency and throttling settings
# Increase delays to avoid rate limiting
CONCURRENT_REQUESTS = 16  # Only 1 request at a time
CONCURRENT_REQUESTS_PER_DOMAIN = 8
DOWNLOAD_DELAY = 3 # 3 seconds between requests

# Disable cookies (enabled by default)
# COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
# TELNETCONSOLE_ENABLED = False

# Override the default request headers:
# DEFAULT_REQUEST_HEADERS = {
#    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#    "Accept-Language": "en",
# }

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
# SPIDER_MIDDLEWARES = {
#    "base_scrapper.middlewares.BaseScrapperSpiderMiddleware": 543,
# }

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
# DOWNLOADER_MIDDLEWARES = {
#    "base_scrapper.middlewares.BaseScrapperDownloaderMiddleware": 543,
# }

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
# EXTENSIONS = {
#    "scrapy.extensions.telnet.TelnetConsole": None,
# }

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
# ITEM_PIPELINES = {
#    "base_scrapper.pipelines.BaseScrapperPipeline": 300,
# }

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
# AUTOTHROTTLE_ENABLED = True
# The initial download delay
# AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
# AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
# AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
# AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = "httpcache"
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
FEED_EXPORT_ENCODING = "utf-8"

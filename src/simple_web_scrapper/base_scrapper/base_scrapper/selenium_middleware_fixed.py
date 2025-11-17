    """
    Fixed Selenium middleware that handles invalid sessions by recreating the driver.
    """
    from scrapy import signals
    from scrapy.http import HtmlResponse
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.common.exceptions import (
        InvalidSessionIdException,
        WebDriverException,
        TimeoutException
    )
    from scrapy_selenium import SeleniumMiddleware
    import logging

    logger = logging.getLogger(__name__)


    class RobustSeleniumMiddleware(SeleniumMiddleware):
        """
        Enhanced Selenium middleware that recreates driver on session failures.
        """
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._driver_failed = False
        
        @classmethod
        def from_crawler(cls, crawler):
            middleware = super().from_crawler(crawler)
            crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
            return middleware
        
        def _recreate_driver(self):
            """Safely recreate the driver after a failure."""
            logger.warning("Recreating Chrome driver due to session failure")
            try:
                if self.driver:
                    self.driver.quit()
            except:
                pass
            
            # Create new driver using parent class method
            self.driver = self._create_driver()
            self._driver_failed = False
            logger.info("Chrome driver successfully recreated")
        
        def _create_driver(self):
            """Create a fresh driver instance."""
            # Use the parent class's driver creation logic
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            
            chrome_options = Options()
            for argument in self.selenium_driver_arguments:
                chrome_options.add_argument(argument)
            
            if self.selenium_browser_executable_path:
                chrome_options.binary_location = self.selenium_browser_executable_path
            
            service = Service(executable_path=self.selenium_driver_executable_path)
            
            driver = webdriver.Chrome(
                service=service,
                options=chrome_options
            )
            
            return driver
        
        def process_request(self, request, spider):
            """Process request with automatic driver recreation on failures."""
            if not isinstance(request, self.selenium_request_cls):
                return None
            
            # Try up to 2 times (original attempt + 1 retry with new driver)
            for attempt in range(2):
                try:
                    if self._driver_failed or not self.driver:
                        self._recreate_driver()
                    
                    # Original scrapy-selenium logic
                    self.driver.get(request.url)
                    
                    if request.wait_until:
                        WebDriverWait(self.driver, request.wait_time).until(
                            request.wait_until
                        )
                    
                    if request.screenshot:
                        request.meta['screenshot'] = self.driver.get_screenshot_as_png()
                    
                    if request.script:
                        self.driver.execute_script(request.script)
                    
                    body = str.encode(self.driver.page_source)
                    request.meta.update({'driver': self.driver})
                    
                    return HtmlResponse(
                        self.driver.current_url,
                        body=body,
                        encoding='utf-8',
                        request=request
                    )
                    
                except InvalidSessionIdException as e:
                    logger.error(f"Invalid session on attempt {attempt + 1} for {request.url}: {e}")
                    self._driver_failed = True
                    
                    if attempt == 0:
                        # Retry once with new driver
                        continue
                    else:
                        # Give up after retry
                        logger.error(f"Failed to recover session for {request.url}")
                        raise
                        
                except TimeoutException as e:
                    logger.warning(f"Timeout waiting for {request.url}: {e}")
                    # Don't recreate driver for timeouts, just fail the request
                    raise
                    
                except WebDriverException as e:
                    logger.error(f"WebDriver error on attempt {attempt + 1} for {request.url}: {e}")
                    self._driver_failed = True
                    
                    if attempt == 0:
                        continue
                    else:
                        raise
            
            # Should not reach here
            return None
        
        def spider_closed(self):
            """Clean up driver on spider close."""
            try:
                if self.driver:
                    self.driver.quit()
                    logger.info("Chrome driver closed successfully")
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")
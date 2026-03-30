from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def test_homepage():
    options = Options()
    driver = webdriver.Chrome(options=options)

    try:
        driver.get("http://localhost:8080")

        wait = WebDriverWait(driver, 10)

        body = wait.until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        assert body.is_displayed()
        print("Homepage loaded successfully.")

    finally:
        driver.quit()

if __name__ == "__main__":
    test_homepage()
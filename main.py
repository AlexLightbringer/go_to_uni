import re
import time
import requests
from bs4 import BeautifulSoup
import telebot
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

# === Настройки ===
BOT_TOKEN = "6041548049:AAEvExz7ykJOTwWF2crh0oaDfGe7r8j1lFU"
USER_UNIQUE_ID = "3572733"
USER_CHAT_ID = 901147319
URL = "https://urfu.ru/ru/ratings-today/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

MAX_RETRIES = 3           # Максимальное число попыток загрузки страницы
WAIT_TIMEOUT = 120        # Таймаут ожидания элементов на странице (секунд)

bot = telebot.TeleBot(BOT_TOKEN)
last_message = None       # Для отслеживания последнего отправленного сообщения


def check_page_access():
    """
    Проверяет доступность страницы по URL.
    Возвращает True, если код ответа 200, иначе False.
    """
    try:
        r = requests.get(URL, headers=HEADERS, timeout=10)
        print(f"HTTP Status Code: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print("Ошибка при доступе к странице:", e)
        return False


def parse_with_selenium():
    """
    Использует Selenium для загрузки страницы,
    кликает по нужному институту и форме обучения,
    ожидает появления ID абитуриента на странице,
    и возвращает HTML-код страницы.
    При неудаче повторяет попытки до MAX_RETRIES раз.
    """
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # Запуск без GUI
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    html = None

    try:
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[INFO] Попытка загрузки страницы #{attempt}")
            driver.get(URL)

            try:
                # Ждём таблицу с институтами
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.menu-departments")))
                rows = driver.find_elements(By.CSS_SELECTOR, "table.menu-departments tr")

                found = False
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 2:
                        continue
                    if "Уральский гуманитарный институт" in cells[0].text:
                        links = cells[1].find_elements(By.TAG_NAME, "a")
                        for link in links:
                            if "Очная" in link.text:
                                print("✅ Кликаем по 'Очная'")
                                driver.execute_script("arguments[0].click();", link)
                                found = True
                                break
                    if found:
                        break

                if not found:
                    print("❌ Институт/форма обучения не найдены.")
                    continue

                # Ждём появления ID абитуриента
                wait.until(EC.presence_of_element_located((By.XPATH, f"//td[text()='{USER_UNIQUE_ID}']")))
                print("✅ ID абитуриента найден на странице")
                html = driver.page_source
                break

            except TimeoutException:
                print(f"[WARNING] Попытка #{attempt} не удалась. Перезагружаем...")
                time.sleep(5)

    finally:
        driver.quit()

    return html


def find_user_info(html):
    """
    Парсит HTML страницы BeautifulSoup, ищет таблицы с данными,
    извлекает информацию по абитуриенту: позиция, согласие, приоритет,
    баллы, план приёма, направление и статус прохождения.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("table.supp")

    user_info = None
    for i in range(len(tables)):
        header = tables[i]
        if "Основные места в рамках КЦП" not in header.text:
            continue

        plan = 0
        direction = ""
        try:
            plan_text = header.find("th", string="План приема").find_next_sibling("td").text.strip()
            plan = int(plan_text)
        except Exception:
            pass

        # Получаем направление (образовательную программу)
        try:
            direction = header.find("th", string=re.compile("Направление")).find_next_sibling("td").text.strip()
        except Exception:
            direction = ""

        if i + 1 < len(tables):
            data_table = tables[i + 1]
            rows = data_table.find_all("tr")[1:]  # Пропускаем заголовок
            for pos, row in enumerate(rows, start=1):
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                ab_id = cols[1].get_text(strip=True)
                if ab_id == USER_UNIQUE_ID:
                    consent = cols[2].get_text(strip=True)
                    priority = cols[3].get_text(strip=True)
                    score = cols[-2].get_text(strip=True)
                    user_info = {
                        "position": pos,
                        "consent": consent,
                        "priority": priority,
                        "score": score,
                        "plan": plan,
                        "inside": pos <= plan,
                        "direction": direction
                    }
                    return user_info
    return None


def send_telegram(info):
    """
    Формирует и отправляет сообщение в Telegram,
    если информация изменилась с прошлого раза.
    """
    global last_message
    inside_str = "✅ ВХОДИТ В КОНКУРС!" if info['inside'] else "❌ НЕ ВХОДИТ в конкурс"

    message = (
        f"📊 УРФУ Рейтинг\n"
        f"ID: {USER_UNIQUE_ID}\n"
        f"🏫 Направление: {info['direction']}\n\n"
        f"📍 Позиция: {info['position']} / {info['plan']}\n"
        f"📩 Согласие: {info['consent']}\n"
        f"📝 Приоритет: {info['priority']}\n"
        f"🏆 Баллы: {info['score']}\n"
        f"{inside_str}"
    )

    if info['inside']:
        message += "\n🎉 Поздравляю! Ты поступил!"

    if message != last_message:
        bot.send_message(USER_CHAT_ID, message)
        print("[INFO] Отправлено сообщение в Telegram.")
        last_message = message
    else:
        print("[INFO] Без изменений — сообщение не отправлялось.")


def run():
    """
    Главный цикл работы бота.
    Проверяет доступность сайта,
    запускает парсинг и отправляет уведомления раз в час.
    """
    print("[INFO] Запуск проверки УРФУ...")
    while True:
        try:
            if not check_page_access():
                time.sleep(300)  # Если сайт недоступен — ждём 5 минут
                continue

            html = parse_with_selenium()
            if not html:
                print("❌ Не удалось загрузить страницу с результатами.")
                time.sleep(300)
                continue

            user_info = find_user_info(html)
            if user_info:
                send_telegram(user_info)
            else:
                print("⚠️ Абитуриент не найден в таблице.")
                bot.send_message(USER_CHAT_ID, "⚠️ Абитуриент не найден в таблице.")
        except Exception as e:
            print("[ERROR]", e)

        time.sleep(3600)  # Запускать раз в час


if __name__ == "__main__":
    run()

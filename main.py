import re
import time
import requests
from bs4 import BeautifulSoup
import telebot
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.chrome.service import Service as ChromeService
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

URLS_TO_CHECK = [
    "https://urfu.ru/ru/ratings-today/",
    "https://www.dvfu.ru/admission/spd/"
]

def check_pages_access(urls):
    all_ok = True
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            print(f"HTTP Status Code for {url}: {r.status_code}")
            if r.status_code != 200:
                print(f"❌ Страница {url} недоступна")
                all_ok = False
        except Exception as e:
            print(f"Ошибка при доступе к странице {url}: {e}")
            all_ok = False
    return all_ok

def parse_with_selenium():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
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

        try:
            direction = header.find("th", string=re.compile("Направление")).find_next_sibling("td").text.strip()
        except Exception:
            direction = ""

        if i + 1 < len(tables):
            data_table = tables[i + 1]
            rows = data_table.find_all("tr")[1:]
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
DIRECTIONS_TO_CHECK = [
    "58.03.01 Востоковедение и африканистика",
    "45.05.01 Перевод и переводоведение",
    "45.03.02 Лингвистика (Перевод и переводоведение (европейские языки))",
    "41.03.01 Зарубежное регионоведение",
    "45.03.02 Лингвистика (Перевод и лингвопереводческие технологии (английский и китайский языки))",
    "45.03.02 Лингвистика (Межкультурная коммуникация (английский и китайский языки))",
    "44.03.05 Педагогическое образование (с двумя профилями подготовки) (Иностранный язык (английский) и Иностранный язык (корейский))",
    "44.03.05 Педагогическое образование (с двумя профилями подготовки) (Иностранный язык (английский) и Иностранный язык (китайский))",
]

def dvfu_check_all_directions(driver, wait, user_id):
    all_messages = []

    for direction_to_select in DIRECTIONS_TO_CHECK:
        try:
            driver.get("https://www.dvfu.ru/admission/spd/")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form[name='arrFilter']")))

            for i in range(5):
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "form[name='arrFilter'] select")) >= i + 1)
                selects = driver.find_elements(By.CSS_SELECTOR, "form[name='arrFilter'] select")

                if i == 0:
                    Select(selects[i]).select_by_visible_text("Бакалавриат/Специалитет")
                elif i == 1:
                    Select(selects[i]).select_by_visible_text("Бюджет")
                elif i == 2:
                    Select(selects[i]).select_by_visible_text("Очная")
                elif i == 3:
                    Select(selects[i]).select_by_visible_text(direction_to_select)
                elif i == 4:
                    Select(selects[i]).select_by_visible_text("По приоритету")

            driver.find_element(By.CSS_SELECTOR, "input.btn.btn-primary[value='Показать']").click()
            WebDriverWait(driver, WAIT_TIMEOUT).until(lambda d: "Общий конкурс" in d.page_source)

            soup = BeautifulSoup(driver.page_source, "html.parser")

            quota_number = 0
            quota_header = soup.find("h4", string="Общий конкурс")
            if quota_header:
                small = quota_header.find_next("small")
                quota_text = small.get_text() if small else ""
                match = re.search(r"Квота:\s*(\d+)", quota_text)
                if match:
                    quota_number = int(match.group(1))

            td_list = soup.select("td.text-left")
            my_index = None
            passed_count = 0

            for i, td in enumerate(td_list):
                if user_id in td.text:
                    my_index = i
                    break

            if my_index is None:
                continue

            for td in td_list[:my_index]:
                collapse = td.find("div", class_="collapse")
                if collapse and all(s in collapse.text for s in [
                    "Согласие на зачисление: Да",
                    "Основной высший приоритет: Да",
                    "Проходной высший приоритет: Да"
                ]):
                    passed_count += 1

            position = passed_count + 1

            user_td = td_list[my_index]
            collapse = user_td.find("div", class_="collapse")
            consent = "Нет"
            priority = "Неизвестно"
            score = "Неизвестно"

            if collapse:
                paragraphs = collapse.find_all("p")
                for p in paragraphs:
                    text = p.get_text(strip=True)
                    if "Согласие на зачисление" in text:
                        consent = "Да" if "Да" in text else "Нет"
                    elif "Сумма баллов" in text:
                        score_match = re.search(r"(\d+)", text)
                        if score_match:
                            score = score_match.group(1)
                    elif "Приоритет" in text:
                        prio_match = re.search(r"(\d+)", text)
                        if prio_match:
                            priority = prio_match.group(1)

            message = (
                f"📊 ДВФУ Рейтинг\n"
                f"ID: {user_id}\n"
                f"🏫 Направление: {direction_to_select}\n\n"
                f"📍 Позиция: {position} / {quota_number}\n"
                f"📩 Согласие: {consent}\n"
                f"📝 Приоритет: {priority}\n"
                f"🏆 Баллы: {score}\n"
            )

            if position <= quota_number:
                message += "🎉 Поздравляю! Ты поступил!"
            else:
                message += f"⏳ До прохода не хватает: {position - quota_number}"

            all_messages.append(message)

        except Exception as e:
            print(f"[DVFU] ❌ Ошибка при направлении {direction_to_select}:", e)

    full_message = "\n\n".join(all_messages)
    if full_message:
        bot.send_message(USER_CHAT_ID, full_message)


def send_telegram(info):
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
    print("[INFO] Запуск проверки УРФУ и ДВФУ...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    service = ChromeService()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        while True:
            try:
                if not check_pages_access(URLS_TO_CHECK):
                    print("[WARNING] Один или несколько сайтов недоступны. Ждём 5 минут...")
                    time.sleep(300)
                    continue

                html = parse_with_selenium()
                if not html:
                    print("❌ Не удалось загрузить страницу с результатами УрФУ.")
                    time.sleep(300)
                    continue

                user_info = find_user_info(html)
                if user_info:
                    send_telegram(user_info)
                    dvfu_check_all_directions(driver, wait, USER_UNIQUE_ID)
                else:
                    print("⚠️ Абитуриент не найден в таблице УрФУ.")
                    bot.send_message(USER_CHAT_ID, "⚠️ Абитуриент не найден в таблице УрФУ.")
            except Exception as e:
                print("[ERROR]", e)

            time.sleep(3600)

    finally:
        driver.quit()

if __name__ == "__main__":
    run()


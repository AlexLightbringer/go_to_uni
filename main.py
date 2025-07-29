# --- Импорт внешних зависимостей ---
# Основные библиотеки для работы с HTTP, HTML-парсингом, Telegram и Selenium
import re
import time
import requests
from bs4 import BeautifulSoup
import telebot
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


# === Конфигурация ===
# Telegram-бот
BOT_TOKEN = "6041548049:AAEvExz7ykJOTwWF2crh0oaDfGe7r8j1lFU"
USER_UNIQUE_ID = "3572733"
USER_CHAT_IDS = ["901147319", "6720399641"]

# URL'ы для проверки доступности
URL = "https://urfu.ru/ru/ratings-today/"
URLS_TO_CHECK = [
    "https://urfu.ru/ru/ratings-today/",
    "https://www.dvfu.ru/admission/spd/",
    "https://urfu.ru/ru/ratings/"
]
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Поведение парсера
MAX_RETRIES = 3           # Максимальное число повторов при ошибках загрузки
WAIT_TIMEOUT = 120        # Максимальное ожидание элементов в DOM (секунды)

# Инициализация Telegram-бота
bot = telebot.TeleBot(BOT_TOKEN)
last_message = None  # Последнее отправленное сообщение для анти-спама


def check_pages_access(urls):
    """Проверка доступности всех целевых страниц."""
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


def parse_urfu_today(driver, wait, user_id):
    """
    Парсит оперативный рейтинг УрФУ, находит нужного абитуриента по ID
    и извлекает его позицию, баллы, согласие и приоритет.
    """
    # Переинициализируем headless-драйвер (важно для изоляции между вызовами)
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
                # Ожидаем таблицу направлений
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.menu-departments")))
                rows = driver.find_elements(By.CSS_SELECTOR, "table.menu-departments tr")

                # Ищем "Уральский гуманитарный институт" -> Очная форма
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

                # Ждём появления нужного ID абитуриента
                wait.until(EC.presence_of_element_located((By.XPATH, f"//td[text()='{USER_UNIQUE_ID}']")))
                print("✅ ID абитуриента найден на странице")
                html = driver.page_source
                break

            except TimeoutException:
                print(f"[WARNING] Попытка #{attempt} не удалась. Перезагружаем...")
                time.sleep(5)

    finally:
        driver.quit()

    if not html:
        return []

    # Парсим таблицы и формируем сообщения
    infos = find_user_info(html)
    messages = []
    for info in infos:
        msg = (
            f"📊 УРФУ Оперативный рейтинг\n"
            f"ID: {info['id']}\n"
            f"🏫 Направление: {info['direction']}\n\n"
            f"📍 Позиция: {info['position']} / {info['plan']}\n"
            f"📩 Согласие: {info['consent']}\n"
            f"📝 Приоритет: {info['priority']}\n"
            f"🏆 Баллы: {info['score']}\n"
        )
        msg += "🎉 Поздравляю! Ты проходишь!"
        messages.append(msg)

    return messages


def find_user_info(html):
    """
    Извлекает из HTML-таблиц все рейтинги, в которых фигурирует пользователь.
    Сравнивает баллы и определяет позицию среди подавших согласие.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("table.supp")
    results = []

    for i in range(len(tables)):
        header = tables[i]
        if "Основные места в рамках КЦП" not in header.text:
            continue

        # Извлекаем план приёма
        try:
            plan_text = header.find("th", string="План приема").find_next_sibling("td").text.strip()
            plan = int(plan_text)
        except:
            continue

        # Извлекаем направление
        try:
            direction = header.find("th", string=re.compile("Направление")).find_next_sibling("td").text.strip()
        except:
            direction = "Неизвестное направление"

        # Получаем таблицу рейтинга
        if i + 1 >= len(tables):
            continue

        data_table = tables[i + 1]
        rows = data_table.find_all("tr")[1:]

        user_score = None
        user_priority = None
        user_consent = None
        found_row = None

        # Поиск строки пользователя
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            ab_id = cols[1].get_text(strip=True)
            if ab_id == USER_UNIQUE_ID:
                found_row = row
                user_consent = cols[2].get_text(strip=True)
                user_priority = cols[3].get_text(strip=True)
                try:
                    user_score = int(cols[-2].get_text(strip=True))
                except:
                    user_score = 0
                break

        if not found_row:
            continue

        # Расчёт позиции среди подавших согласие
        position = 1
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            consent = cols[2].get_text(strip=True)
            if consent != "Да":
                continue
            try:
                score = int(cols[-2].get_text(strip=True))
            except:
                continue
            if score > user_score:
                position += 1
            elif score == user_score:
                other_id = cols[1].get_text(strip=True)
                if other_id != USER_UNIQUE_ID:
                    position += 1

        inside = position <= plan
        if inside:
            results.append({
                "id": USER_UNIQUE_ID,
                "position": position,
                "consent": user_consent,
                "priority": user_priority,
                "score": user_score,
                "plan": plan,
                "inside": inside,
                "direction": direction
            })

    return results

MAJORS_TO_CHECK = [
    "45.05.01 Перевод и переводоведение",
    "58.03.01 Востоковедение и африканистика",
    "45.03.02 Лингвистика (Перевод и переводоведение (европейские языки))",
    "41.03.01 Зарубежное регионоведение",
    "45.03.02 Лингвистика (Перевод и лингвопереводческие технологии (английский и китайский языки))",
    "45.03.02 Лингвистика (Межкультурная коммуникация (английский и китайский языки))",
    "44.03.05 Педагогическое образование (с двумя профилями подготовки) (Иностранный язык (английский) и Иностранный язык (корейский))",
    "44.03.05 Педагогическое образование (с двумя профилями подготовки) (Иностранный язык (английский) и Иностранный язык (китайский))",
]


def dvfu_check_all_majors(driver, wait, user_id):
    """
    Проходит по каждому направлению в ДВФУ, применяет фильтры, парсит таблицу "Общий конкурс",
    находит абитуриента по ID и определяет, входит ли он в квоту.
    """
    all_messages = []

    for major_to_select in MAJORS_TO_CHECK:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                print(f"[DVFU] 🔍 Попытка #{attempt} — Проверка направления: {major_to_select}")
                driver.get("https://www.dvfu.ru/admission/spd/")
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form[name='arrFilter']")))

                # Последовательно выбираем значения в выпадающих списках фильтра
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
                        Select(selects[i]).select_by_visible_text(major_to_select)
                    elif i == 4:
                        Select(selects[i]).select_by_visible_text("По сумме баллов")

                # Отправляем фильтр
                driver.find_element(By.CSS_SELECTOR, "input.btn.btn-primary[value='Показать']").click()
                wait.until(lambda d: "Общий конкурс" in d.page_source)

                soup = BeautifulSoup(driver.page_source, "html.parser")

                # Ищем нужный блок "Общий конкурс"
                block_row = soup.find("tr", class_="block-header")
                while block_row:
                    h4 = block_row.find("h4")
                    if h4 and h4.get_text(strip=True) == "Общий конкурс":
                        break
                    block_row = block_row.find_next("tr", class_="block-header")
                else:
                    print(f"[DVFU] ⚠️ Не найден блок 'Общий конкурс' для направления: {major_to_select}")
                    break

                # Получаем квоту с места
                quota_number = 0
                small = block_row.find("small")
                if small:
                    match = re.search(r"Квота:\s*(\d+)", small.get_text())
                    if match:
                        quota_number = int(match.group(1))

                # Переходим к таблице рейтинга
                table = block_row.find_next_sibling("tr")
                while table and not table.find_all("td"):
                    table = table.find_next_sibling("tr")

                if not table:
                    print(f"[DVFU] ⚠️ Не найдена таблица под 'Общий конкурс'")
                    break

                # Собираем строки таблицы с данными
                data_rows = []
                row = table
                while row and not row.get("class") == ["block-header"]:
                    if row.find("td", class_="text-left"):
                        data_rows.append(row)
                    row = row.find_next_sibling("tr")

                # Ищем строку пользователя
                my_row = None
                my_index = None
                passed_count = 0

                for i, r in enumerate(data_rows):
                    td = r.find("td", class_="text-left")
                    if td and user_id in td.text:
                        my_row = r
                        my_index = i
                        break

                if my_row is None:
                    print(f"[DVFU] ❌ Абитуриент с ID {user_id} не найден")
                    break

                # Позиция по индексу или значению
                position_td = my_row.find("td")
                if position_td and position_td.text.strip().isdigit():
                    position = int(position_td.text.strip())
                else:
                    position = my_index + 1

                # Считаем количество поступающих выше по приоритету и согласию
                for r in data_rows[:my_index]:
                    collapse = r.find("div", class_="collapse")
                    if collapse:
                        text = collapse.get_text()
                        if "Согласие на зачисление: Да" in text and "Проходной высший приоритет: Да" in text:
                            passed_count += 1

                # Извлекаем дополнительные параметры из своей строки
                user_td = my_row.find("td", class_="text-left")
                collapse = user_td.find("div", class_="collapse") if user_td else None
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
                            match = re.search(r"(\d+)", text)
                            if match:
                                score = match.group(1)
                        elif "Приоритет" in text:
                            match = re.search(r"(\d+)", text)
                            if match:
                                priority = match.group(1)

                # Формируем Telegram-сообщение
                message = (
                    f"📊 ДВФУ Рейтинг\n"
                    f"ID: {user_id}\n"
                    f"🏫 Направление: {major_to_select}\n\n"
                    f"📍 Позиция: {passed_count} / {quota_number}\n"
                    f"📩 Согласие: {consent}\n"
                    f"📝 Приоритет: {priority}\n"
                    f"🏆 Баллы: {score}\n"
                )

                if passed_count < quota_number:
                    message += f"🎉 Поздравляю! Ты проходишь! До тебя занято мест: {passed_count - 1} из {quota_number}"
                else:
                    message += f"⏳ Пока не проходишь. До тебя уже занято мест: {passed_count} из {quota_number}"

                all_messages.append(message)
                break  # Успешная попытка, прерываем retries

            except Exception as e:
                print(f"[DVFU] ❌ Ошибка при попытке #{attempt} для {major_to_select}: {e}")
                if attempt == MAX_RETRIES:
                    print(f"[DVFU] ❌ Превышено число попыток для направления: {major_to_select}")

    return all_messages


def send_telegram(info):
    """
    Отправляет информацию о рейтинге в Telegram, если сообщение отличается от предыдущего.
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
        for chat_id in USER_CHAT_IDS:
            bot.send_message(chat_id, message)
        print("[INFO] Отправлено сообщение в Telegram.")
        last_message = message
    else:
        print("[INFO] Без изменений — сообщение не отправлялось.")


def parse_urfu_all_majors(driver, wait, user_id):
    """
    Парсит рейтинг поступающих по всем направлениям (очная форма обучения)
    на странице https://urfu.ru/ru/ratings/ и определяет позицию абитуриента
    по ID с учётом согласия на зачисление.

    :param driver: экземпляр Selenium WebDriver
    :param wait: экземпляр WebDriverWait
    :param user_id: уникальный ID абитуриента
    :return: список сообщений по направлениям, где найден абитуриент
    """
    messages = []

    try:
        print("[URFU:all] Загружаем страницу рейтингов по всем направлениям...")
        driver.get("https://urfu.ru/ru/ratings/")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.menu-departments")))

        # Переход к "Уральский гуманитарный институт", форма "Очная"
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
                        print("[URFU:all] Переход к рейтингу 'Очная' формы...")
                        driver.execute_script("arguments[0].click();", link)
                        found = True
                        break
            if found:
                break

        if not found:
            print("❌ Гуманитарный институт 'Очная' не найден.")
            return messages

        # Ждём появления таблиц рейтингов
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.supp")))
        print("[URFU:all] Загружены таблицы рейтингов, начинаем анализ...")

        soup = BeautifulSoup(driver.page_source, "html.parser")
        tables = soup.select("table.supp")

        for i in range(len(tables)):
            header = tables[i]
            if "Основные места в рамках КЦП" not in header.text:
                continue

            # Чтение плана приёма
            try:
                plan_text = header.find("th", string="План приема").find_next_sibling("td").text.strip()
                plan = int(plan_text)
            except:
                continue  # пропускаем, если невозможно получить план

            # Чтение направления
            try:
                direction = header.find("th", string=re.compile("Направление")).find_next_sibling("td").text.strip()
            except:
                direction = "Неизвестное направление"

            if i + 1 >= len(tables):
                continue  # нет таблицы с данными после заголовка

            data_table = tables[i + 1]
            rows = data_table.find_all("tr")[1:]  # пропускаем заголовок

            user_score = None
            user_priority = None
            user_consent = None
            found_row = None

            # Поиск строки абитуриента
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                ab_id = cols[1].get_text(strip=True)
                if ab_id == user_id:
                    found_row = row
                    user_consent = cols[2].get_text(strip=True)
                    user_priority = cols[3].get_text(strip=True)
                    try:
                        user_score = int(cols[-2].get_text(strip=True))
                    except:
                        user_score = 0
                    break

            if not found_row:
                continue  # не найден в этом направлении

            # Подсчёт позиции среди абитуриентов с согласием
            position = 1
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                consent = cols[2].get_text(strip=True)
                if consent != "Да":
                    continue
                try:
                    score = int(cols[-2].get_text(strip=True))
                except:
                    continue
                if score > user_score:
                    position += 1
                elif score == user_score:
                    other_id = cols[1].get_text(strip=True)
                    if other_id != user_id:
                        position += 1

            inside = position <= plan

            status_line = (
                f"🎉 Поздравляю! Ты проходишь! До тебя занято мест: {position - 1} из {plan}"
                if inside else
                f"⏳ Пока не проходишь. До тебя уже занято мест: {position} из {plan}"
            )

            msg = (
                f"📊 УрФУ Рейтинг (все направления)\n"
                f"ID: {user_id}\n"
                f"🏫 Направление: {direction}\n\n"
                f"📍 Позиция среди подавших согласие: {position} / {plan}\n"
                f"📩 Согласие: {user_consent}\n"
                f"📝 Приоритет: {user_priority}\n"
                f"🏆 Баллы: {user_score}\n"
                f"{status_line}"
            )
            messages.append(msg)

            print(f"[URFU:all] ✅ Найдено направление: {direction}, позиция среди 'Да': {position}/{plan}")

    except Exception as e:
        print("[URFU:all] ❌ Ошибка при парсинге всех направлений:", e)
        return []

    return messages


def run():
    """
    Точка входа. Последовательно выполняет:
    - Проверку доступности сайтов
    - Парсинг ДВФУ, УрФУ (все направления), УрФУ (оперативный рейтинг)
    - Отправку сообщений в Telegram по категориям
    """
    check_pages_access(URLS_TO_CHECK)

    # Инициализация Selenium-драйвера
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 15)

    try:
        print("▶️ Запуск проверки направлений ДВФУ...")
        dvfu_messages = dvfu_check_all_majors(driver, wait, USER_UNIQUE_ID)

        print("▶️ Запуск проверки УрФУ (основной рейтинг)...")
        urfu_all_majors_messages = parse_urfu_all_majors(driver, wait, USER_UNIQUE_ID)

        print("▶️ Запуск проверки УрФУ (оперативный рейтинг)...")
        urfu_today_messages = parse_urfu_today(driver, wait, USER_UNIQUE_ID)

        # Отправка результатов по категориям в Telegram
        if dvfu_messages:
            dvfu_block = "📚 Все направления ДВФУ:\n\n" + "\n\n".join(dvfu_messages)
            for chat_id in USER_CHAT_IDS:
                bot.send_message(chat_id, dvfu_block)

        if urfu_all_majors_messages:
            urfu_all_block = "📘 УрФУ (все направления):\n\n" + "\n\n".join(urfu_all_majors_messages)
            for chat_id in USER_CHAT_IDS:
                bot.send_message(chat_id, urfu_all_block)

        if urfu_today_messages:
            urfu_today_block = "⚡ УрФУ (оперативный рейтинг):\n\n" + "\n\n".join(urfu_today_messages)
            for chat_id in USER_CHAT_IDS:
                bot.send_message(chat_id, urfu_today_block)

        if not any([dvfu_messages, urfu_all_majors_messages, urfu_today_messages]):
            print("❌ Ни один рейтинг не содержит нужного ID.")

    except Exception as e:
        print("❌ Общая ошибка во время выполнения run():", e)
    finally:
        driver.quit()


# Циклический запуск — каждые 60 минут
if __name__ == "__main__":
    while True:
        print("⏰ Запуск проверки...")
        run()
        print("🕒 Ожидание 1 час до следующей проверки...")
        time.sleep(3600)

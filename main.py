import os
import base64
import telebot
from openai import OpenAI

# Отримуємо ключі з системних змінних Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)

# Системний промпт із закладеною матрицею часу та логікою аналізу
SYSTEM_PROMPT = (
    "Ти — професійний торговий асистент `@NavigatorMia_bot`, який спеціалізується на аналізі ринкових імпульсів "
    "та матриці часу. Твоє завдання — аналізувати скріншоти графіків, зважаючи на взаємодію таймфреймів "
    "(від 1-хвилинних угод до 15-хвилинних, 1-годинних і 4-годинних інтервалів). "
    "Шукай точки відкриття та закриття свічок, оцінюй перетини часових інтервалів, точки зламу імпульсів "
    "та давай чіткий, структурований висновок із погляду ринкової динаміки."
)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message, 
        "Привіт! Я твій торговий асистент `@NavigatorMia_bot`. "
        "Матриця часу активована. Надішли мені скріншот графіку, і я проаналізую його "
        "з урахуванням взаємодії таймфреймів та свічкових перетинів!"
    )

# Допоміжна функція для кодування зображення
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# Обробка фотографій із застосуванням матриці часу
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        bot.reply_to(message, "⏳ Аналізую графік крізь призму матриці часу та таймфреймів...")
        
        # Отримуємо файл фото у найкращій якості
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Зберігаємо тимчасово
        image_path = "chart.jpg"
        with open(image_path, 'wb') as new_file:
            new_file.write(downloaded_file)

        # Запит до OpenAI з урахуванням нашої методики
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": (
                                "Проаналізуй цей торговий скріншот. Оціни структуру свічок, "
                                "розпізнай поточний імпульс на 1-хвилинному рівні та співвіднеси його з "
                                "ширшими інтервалами (матрицею часу). Дай чіткий висновок щодо точок входу та ризиків."
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encode_image(image_path)}"}
                        },
                    ],
                }
            ],
            max_tokens=500,
        )
        
        analysis_text = response.choices[0].message.content
        bot.reply_to(message, analysis_text)
        
    except Exception as e:
        bot.reply_to(message, f"Сталася помилка під час аналізу: {e}")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, "Будь ласка, надішли скріншот торгового графіку, щоб я міг застосувати матрицю часу для аналізу.")

if __name__ == '__main__':
    print("Бот запущено з підтримкою матриці часу...")
    bot.infinity_polling()

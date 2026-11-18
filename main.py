import os
import telebot

# Встав сюди НОВИЙ токен, який отримаєш у BotFather
TOKEN = "ВСТАВ_НОВИЙ_ТОКЕН_СЮДИ"

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привіт! Я твій торговий асистент @NavigatorMia_bot. Я на зв'язку!")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"Я отримав твоє повідомлення: {message.text}")

if __name__ == '__main__':
    bot.infinity_polling()

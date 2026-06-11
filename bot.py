import os
import asyncio
import logging
from datetime import datetime, date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
import httpx

# ============ ТОКЕНЫ из переменных окружения ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OURA_TOKEN = os.environ["OURA_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# ============ НАСТРОЙКИ ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Ты персональный фитнес-коуч и нутрициолог по имени Коля-бот. 
Твоя главная цель — помочь пользователю похудеть через умный анализ его данных здоровья.

Пользователь занимается: футбол, падел, зал (gym).
Данные приходят из Oura Ring.

Твой стиль:
- Говори по-русски, дружелюбно и мотивирующе
- Объясняй ПОЧЕМУ, не просто показывай цифры
- Связывай данные между собой (стресс → кортизол → вес, сон → аппетит и т.д.)
- Давай конкретные советы на завтра/сегодня
- Используй эмодзи умеренно
- Будь честным — если что-то не так, говори прямо но с поддержкой

Когда анализируешь данные, всегда объясняй:
1. Что происходит с телом
2. Почему это влияет на похудение
3. Что конкретно сделать

Данные за сегодня/период будут переданы тебе в сообщении."""

# История разговора
conversation_history = []

# ============ OURA API ============
async def get_oura_data(days_back: int = 1) -> dict:
    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)
    
    headers = {"Authorization": f"Bearer {OURA_TOKEN}"}
    base_url = "https://api.ouraring.com/v2/usercollection"
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }
    
    data = {}
    
    async with httpx.AsyncClient() as http_client:
        for endpoint, key in [
            ("sleep", "sleep"),
            ("daily_activity", "activity"),
            ("daily_readiness", "readiness"),
            ("daily_stress", "stress"),
            ("workout", "workouts"),
        ]:
            try:
                r = await http_client.get(f"{base_url}/{endpoint}", headers=headers, params=params)
                data[key] = r.json().get("data", [])
            except Exception as e:
                data[key] = []
                logger.error(f"{key} error: {e}")

    return data

def format_oura_data(data: dict) -> str:
    lines = [f"📊 ДАННЫЕ OURA (сегодня {date.today().isoformat()})\n"]
    
    if data.get("sleep"):
        lines.append("😴 СОН:")
        for s in data["sleep"][-3:]:
            duration = round(s.get("total_sleep_duration", 0) / 3600, 1)
            deep = round(s.get("deep_sleep_duration", 0) / 3600, 1)
            rem = round(s.get("rem_sleep_duration", 0) / 3600, 1)
            lines.append(f"  {s.get('day')}: {duration}ч, скор={s.get('score','?')}, эфф={s.get('efficiency','?')}%, глубокий={deep}ч, REM={rem}ч, HRV={s.get('average_hrv','?')}, ЧСС={s.get('average_heart_rate','?')}")
    
    if data.get("activity"):
        lines.append("\n🏃 АКТИВНОСТЬ:")
        for a in data["activity"][-3:]:
            lines.append(f"  {a.get('day')}: шаги={a.get('steps','?')}, калории={a.get('total_calories','?')} (актив={a.get('active_calories','?')}), скор={a.get('score','?')}")
    
    if data.get("readiness"):
        lines.append("\n🔋 ВОССТАНОВЛЕНИЕ:")
        for r in data["readiness"][-3:]:
            c = r.get("contributors", {})
            lines.append(f"  {r.get('day')}: скор={r.get('score','?')}, HRV баланс={c.get('hrv_balance','?')}, восст={c.get('recovery_index','?')}")
    
    if data.get("stress"):
        lines.append("\n😓 СТРЕСС:")
        for s in data["stress"][-3:]:
            lines.append(f"  {s.get('day')}: стресс={s.get('stress_high','?')}мин, восст={s.get('recovery_high','?')}мин, итог={s.get('day_summary','?')}")
    
    if data.get("workouts"):
        lines.append("\n💪 ТРЕНИРОВКИ:")
        for w in data["workouts"][-5:]:
            duration = round(w.get("duration", 0) / 60)
            lines.append(f"  {w.get('day')}: {w.get('activity','?')}, {duration}мин, {w.get('calories','?')}ккал")
    
    return "\n".join(lines) if len(lines) > 1 else "Данные из Oura не получены."

# ============ CLAUDE АГЕНТ ============
async def ask_claude(user_message: str, oura_data_str: str = "") -> str:
    global conversation_history
    
    full_message = user_message
    if oura_data_str:
        full_message = f"{oura_data_str}\n\nВопрос пользователя: {user_message}"
    
    conversation_history.append({"role": "user", "content": full_message})
    
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]
    
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=conversation_history
    )
    
    assistant_message = response.content[0].text
    conversation_history.append({"role": "assistant", "content": assistant_message})
    
    return assistant_message

# ============ TELEGRAM HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Я твой персональный фитнес-коуч.\n\n"
        "Анализирую данные из Oura Ring и помогаю худеть умно.\n\n"
        "Команды:\n"
        "/today — анализ сегодняшнего дня\n"
        "/week — анализ за неделю\n"
        "/tip — совет на сегодня\n\n"
        "Или просто пиши что угодно! 💪"
    )

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Загружаю твои данные из Oura...")
    data = await get_oura_data(days_back=2)
    data_str = format_oura_data(data)
    response = await ask_claude(
        "Сделай полный анализ моего дня. Как я себя чувствую? Что происходит с моим телом? Как это влияет на похудение? Дай конкретные советы на завтра.",
        data_str
    )
    await update.message.reply_text(response)

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Загружаю данные за неделю...")
    data = await get_oura_data(days_back=7)
    data_str = format_oura_data(data)
    response = await ask_claude(
        "Сделай анализ за неделю. Какие тренды? Почему я худею или не худею? Что нужно изменить?",
        data_str
    )
    await update.message.reply_text(response)

async def tip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await get_oura_data(days_back=1)
    data_str = format_oura_data(data)
    response = await ask_claude(
        "Дай мне один конкретный совет на сегодня исходя из моих данных. Коротко и по делу.",
        data_str
    )
    await update.message.reply_text(response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    keywords = ["сегодня", "вчера", "неделя", "данные", "как я", "сон", "стресс",
                "шаги", "калории", "вес", "тренировка", "восстановление", "устал",
                "худею", "прогресс", "результат"]
    
    oura_str = ""
    if any(kw in user_text.lower() for kw in keywords):
        await update.message.reply_text("⏳ Смотрю твои данные...")
        data = await get_oura_data(days_back=3)
        oura_str = format_oura_data(data)
    
    response = await ask_claude(user_text, oura_str)
    await update.message.reply_text(response)

# ============ ЗАПУСК ============
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("tip", tip_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

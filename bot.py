import os
import logging
import sqlite3
from datetime import datetime
from anthropic import Anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv('TELEGRAM_TOKEN')
ANTHROPIC_KEY   = os.getenv('ANTHROPIC_API_KEY')
ADMIN_ID        = int(os.getenv('ADMIN_ID', '0'))
PAYMENT_DETAILS = os.getenv('PAYMENT_DETAILS', 'Реквизиты оплаты — уточните у администратора')
FREE_CREDITS    = 3
DB_PATH         = 'bot.db'

anthropic = Anthropic(api_key=ANTHROPIC_KEY)

# ─── База данных ─────────────────────────────────────────────────────────────
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            credits      INTEGER DEFAULT 3,
            total_gen    INTEGER DEFAULT 0,
            created_at   TEXT,
            is_banned    INTEGER DEFAULT 0
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS generations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            input_text TEXT,
            output_text TEXT,
            created_at TEXT
        )''')

def get_user(user_id: int):
    with db() as con:
        return con.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone()

def ensure_user(user_id: int, username: str, full_name: str):
    with db() as con:
        con.execute(
            'INSERT OR IGNORE INTO users (user_id,username,full_name,credits,total_gen,created_at) VALUES (?,?,?,?,0,?)',
            (user_id, username, full_name, FREE_CREDITS, datetime.now().isoformat())
        )

def deduct_credit(user_id: int):
    with db() as con:
        con.execute('UPDATE users SET credits=credits-1, total_gen=total_gen+1 WHERE user_id=?', (user_id,))

def add_credits(user_id: int, amount: int):
    with db() as con:
        con.execute('UPDATE users SET credits=credits+? WHERE user_id=?', (amount, user_id))

def save_gen(user_id: int, inp: str, out: str):
    with db() as con:
        con.execute(
            'INSERT INTO generations (user_id,input_text,output_text,created_at) VALUES (?,?,?,?)',
            (user_id, inp, out, datetime.now().isoformat())
        )

def get_stats():
    with db() as con:
        total    = con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        gens     = con.execute('SELECT COUNT(*) FROM generations').fetchone()[0]
        new_today = con.execute(
            "SELECT COUNT(*) FROM users WHERE date(created_at)=date('now')"
        ).fetchone()[0]
    return total, gens, new_today

# ─── AI генерация ─────────────────────────────────────────────────────────────
async def generate_card(product_info: str) -> str:
    msg = anthropic.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=1500,
        messages=[{
            'role': 'user',
            'content': (
                'Напиши продающую карточку товара для Wildberries/Ozon.\n\n'
                f'Информация о товаре:\n{product_info}\n\n'
                'Требования:\n'
                '- Заголовок: до 100 символов, главное ключевое слово в начале\n'
                '- Описание: 800-1000 символов, конкретные факты, живой язык\n'
                '- Преимущества: 5-7 пунктов, каждый начинается с ✅\n'
                '- SEO-ключи вписаны естественно\n'
                '- Завершить призывом к действию\n\n'
                'Формат строго:\n'
                '**ЗАГОЛОВОК:**\n[заголовок]\n\n'
                '**ОПИСАНИЕ:**\n[описание]\n\n'
                '**ПРЕИМУЩЕСТВА:**\n[список]'
            )
        }]
    )
    return msg.content[0].text

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def kb_start():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('📋 Как пользоваться', callback_data='howto'),
        InlineKeyboardButton('💳 Купить карточки',  callback_data='buy'),
    ]])

def kb_buy():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('5 карточек — 299 руб',  callback_data='pay_5_299')],
        [InlineKeyboardButton('20 карточек — 990 руб', callback_data='pay_20_990')],
    ])

def kb_out_of_credits():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('💳 Купить карточки', callback_data='buy')
    ]])

# ─── Handlers ────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u.id, u.username or '', u.full_name)
    row = get_user(u.id)
    credits = row[3]
    await update.message.reply_text(
        f'Привет, {u.first_name}! 👋\n\n'
        'Я пишу продающие карточки товаров для *Wildberries* и *Ozon* с SEO-оптимизацией.\n\n'
        '⚡ Одна карточка за 30 секунд\n'
        '🎯 Заголовок + описание + преимущества\n'
        '🔍 Вписаны SEO-ключевые слова\n\n'
        f'💎 Твой баланс: *{credits} карточки*\n\n'
        'Просто отправь описание товара — и я напишу карточку!',
        parse_mode='Markdown',
        reply_markup=kb_start()
    )

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '💰 *Пополнить баланс:*\n\n'
        '• 5 карточек — 299 руб\n'
        '• 20 карточек — 990 руб _(выгоднее на 30%)_\n\n'
        'Выбери пакет 👇',
        parse_mode='Markdown',
        reply_markup=kb_buy()
    )

async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u.id, u.username or '', u.full_name)
    row = get_user(u.id)
    await update.message.reply_text(
        f'💎 Твой баланс: *{row[3]} карточек*\n'
        f'📝 Всего написано: *{row[4]}*',
        parse_mode='Markdown'
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    total, gens, new = get_stats()
    await update.message.reply_text(
        f'📊 *Статистика бота:*\n\n'
        f'👥 Всего пользователей: {total}\n'
        f'📝 Всего генераций: {gens}\n'
        f'🆕 Новых сегодня: {new}',
        parse_mode='Markdown'
    )

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда для выдачи кредитов: /add <user_id> <amount>"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        uid    = int(ctx.args[0])
        amount = int(ctx.args[1])
        add_credits(uid, amount)
        await update.message.reply_text(f'✅ Добавлено {amount} кредитов → {uid}')
        await ctx.bot.send_message(
            uid,
            f'✅ Баланс пополнен на *{amount} карточек*. Можешь продолжать!',
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f'Ошибка: {e}\nФормат: /add <user_id> <amount>')

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == 'howto':
        await q.message.reply_text(
            '📋 *Как пользоваться:*\n\n'
            'Просто отправь описание товара. Например:\n\n'
            '_Термос 500 мл, нержавеющая сталь, держит тепло 12 часов, '
            'вес 280г, цвет серебристый, герметичная крышка, для офиса и туризма_\n\n'
            'Чем больше деталей — тем лучше карточка!\n\n'
            'Можно добавить:\n'
            '• Название и модель\n'
            '• Материал, цвет, размер\n'
            '• Для кого предназначен\n'
            '• Ключевые преимущества',
            parse_mode='Markdown'
        )

    elif data == 'buy':
        await q.message.reply_text(
            '💰 *Пополнить баланс:*\n\n'
            '• 5 карточек — 299 руб\n'
            '• 20 карточек — 990 руб _(выгоднее на 30%)_',
            parse_mode='Markdown',
            reply_markup=kb_buy()
        )

    elif data.startswith('pay_'):
        _, qty, price = data.split('_')
        await q.message.reply_text(
            f'💳 *Оплата {price} руб ({qty} карточек)*\n\n'
            f'{PAYMENT_DETAILS}\n\n'
            f'После оплаты пришли скриншот сюда — активирую карточки в течение часа.',
            parse_mode='Markdown'
        )

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u.id, u.username or '', u.full_name)
    row = get_user(u.id)

    if row[6]:  # is_banned
        return

    credits = row[3]

    if credits <= 0:
        await update.message.reply_text(
            '❌ Карточки закончились. Пополни баланс:',
            reply_markup=kb_out_of_credits()
        )
        return

    text = update.message.text
    if len(text) < 15:
        await update.message.reply_text(
            '✏️ Опиши товар подробнее — название, характеристики, материал, для кого.'
        )
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, 'typing')
    status_msg = await update.message.reply_text('⏳ Пишу карточку...')

    try:
        card = await generate_card(text)
        deduct_credit(u.id)
        save_gen(u.id, text, card)
        remaining = get_user(u.id)[3]

        footer = f'\n\n─────────────────\n💎 Осталось карточек: *{remaining}*'
        if remaining == 0:
            footer += '\n\n💳 /buy — пополнить баланс'

        await status_msg.edit_text(card + footer, parse_mode='Markdown')

        # Уведомление админу
        if ADMIN_ID:
            try:
                await ctx.bot.send_message(
                    ADMIN_ID,
                    f'🔔 Генерация\n'
                    f'Юзер: @{u.username} | id: {u.id}\n'
                    f'Осталось кредитов: {remaining}'
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f'Generation error: {e}')
        await status_msg.edit_text('❌ Ошибка генерации. Попробуй снова через минуту.')

# ─── Запуск ──────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler('start',   cmd_start))
    app.add_handler(CommandHandler('buy',     cmd_buy))
    app.add_handler(CommandHandler('balance', cmd_balance))
    app.add_handler(CommandHandler('stats',   cmd_stats))
    app.add_handler(CommandHandler('add',     cmd_add))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info('Bot is running...')
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()

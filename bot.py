import asyncio
import logging
import sqlite3
from typing import List, Dict, Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest


# 1. КОНФИГУРАЦИЯ


BOT_TOKEN = "8706631877:AAE2OW0owSRzfH7Qso-qZNpeYVQFxKp9lHQ"
ADMIN_ID = 8242557810
MAIN_CHANNEL_INVITE_LINK = "https://t.me/+AAlgnpBWADIzNGZi"


# 2. БАЗА ДАННЫХ (SQLite)


DB_NAME = "bot_database.db"

def init_db():
    """Инициализация базы данных и создание таблицы."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL
            )
        """)
        conn.commit()

def add_channel_to_db(channel_id: str, title: str, url: str):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO channels (channel_id, title, url) VALUES (?, ?, ?)", 
                       (channel_id, title, url))
        conn.commit()

def remove_channel_from_db(db_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channels WHERE id = ?", (db_id,))
        conn.commit()

def get_all_channels() -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels")
        return [dict(row) for row in cursor.fetchall()]


# 3. ФИЛЬТРЫ И МАШИНА СОСТОЯНИЙ (FSM)


class IsAdmin(BaseFilter):
    """Фильтр для проверки прав администратора."""
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == ADMIN_ID

class AddChannelStates(StatesGroup):
    """Состояния для пошагового добавления канала."""
    waiting_for_channel_id = State()
    waiting_for_title = State()
    waiting_for_url = State()


# 4. УТИЛИТЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ


async def check_subscription(bot: Bot, user_id: int, channel_id: str) -> bool:
    """
    Проверяет подписку пользователя на канал.
    Возвращает True, если подписан, и False, если нет.
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        # Статусы, означающие, что человек в канале:
        return member.status in ['member', 'administrator', 'creator']
    except TelegramBadRequest:
        # Если бот не админ в канале или неверный ID
        logging.error(f"Ошибка проверки подписки. Бот не админ в {channel_id} или неверный ID.")
        return False
    except Exception as e:
        logging.error(f"Неизвестная ошибка при проверке канала {channel_id}: {e}")
        return False


# 5. ХЭНДЛЕРЫ: АДМИН-ПАНЕЛЬ


admin_router = Router()
admin_router.message.filter(IsAdmin()) # Применяем фильтр ко всем хэндлерам роутера

@admin_router.message(Command("admin"))
async def admin_panel(message: Message):
    """Главное меню администратора."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список каналов", callback_data="admin_list")
    builder.button(text="➕ Добавить канал", callback_data="admin_add")
    builder.button(text="❌ Удалить канал", callback_data="admin_delete_menu")
    builder.adjust(1)
    
    await message.answer("🛠 <b>Панель администратора</b>\nВыберите действие:", 
                         reply_markup=builder.as_markup())

@admin_router.callback_query(F.data == "admin_list")
async def show_channels(callback: CallbackQuery):
    channels = get_all_channels()
    if not channels:
        await callback.message.answer("Список каналов пуст.")
    else:
        text = "<b>Текущие рекламные каналы:</b>\n\n"
        for idx, ch in enumerate(channels, 1):
            text += f"{idx}. <b>{ch['title']}</b>\nID: <code>{ch['channel_id']}</code>\nСсылка: {ch['url']}\n\n"
        await callback.message.answer(text)
    await callback.answer()

# --- Добавление канала ---

@admin_router.callback_query(F.data == "admin_add")
async def add_channel_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Введите <b>ID канала</b> (например, <code>-1001234567890</code> для закрытых "
        "или <code>@username</code> для открытых).\n\n"
        "<i>Внимание: Бот должен быть администратором в этом канале!</i>"
    )
    await state.set_state(AddChannelStates.waiting_for_channel_id)
    await callback.answer()

@admin_router.message(AddChannelStates.waiting_for_channel_id)
async def add_channel_id(message: Message, state: FSMContext):
    await state.update_data(channel_id=message.text.strip())
    await message.answer("Теперь введите <b>Название канала</b> (оно будет на кнопке):")
    await state.set_state(AddChannelStates.waiting_for_title)

@admin_router.message(AddChannelStates.waiting_for_title)
async def add_channel_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите <b>Ссылку на канал</b> (на нее будет вести кнопка):")
    await state.set_state(AddChannelStates.waiting_for_url)

@admin_router.message(AddChannelStates.waiting_for_url)
async def add_channel_url(message: Message, state: FSMContext):
    url = message.text.strip()
    data = await state.get_data()
    
    add_channel_to_db(channel_id=data['channel_id'], title=data['title'], url=url)
    await message.answer(f"✅ Канал <b>{data['title']}</b> успешно добавлен!")
    await state.clear()

# Удаление канала

@admin_router.callback_query(F.data == "admin_delete_menu")
async def delete_channel_menu(callback: CallbackQuery):
    channels = get_all_channels()
    if not channels:
        await callback.message.answer("Нет каналов для удаления.")
        return await callback.answer()

    builder = InlineKeyboardBuilder()
    for ch in channels:
        # В callback_data передаем ID записи в БД
        builder.button(text=f"Удалить: {ch['title']}", callback_data=f"del_ch_{ch['id']}")
    builder.adjust(1)
    
    await callback.message.answer("Выберите канал, который хотите удалить:", 
                                  reply_markup=builder.as_markup())
    await callback.answer()

@admin_router.callback_query(F.data.startswith("del_ch_"))
async def delete_channel_confirm(callback: CallbackQuery):
    db_id = int(callback.data.split("_")[2])
    remove_channel_from_db(db_id)
    await callback.message.edit_text("✅ Канал успешно удален.")
    await callback.answer()



# 6. ХЭНДЛЕРЫ: ПОЛЬЗОВАТЕЛИ (ПРОВЕРКА ПОДПИСКИ)


user_router = Router()

async def get_unsubscribed_channels(bot: Bot, user_id: int) -> List[Dict[str, Any]]:
    """Возвращает список каналов, на которые пользователь ЕЩЕ НЕ подписан."""
    channels = get_all_channels()
    unsubscribed = []
    
    for ch in channels:
        is_subbed = await check_subscription(bot, user_id, ch['channel_id'])
        if not is_subbed:
            unsubscribed.append(ch)
            
    return unsubscribed

def create_subscription_keyboard(unsubscribed_channels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопками неподписанных каналов и кнопкой проверки."""
    builder = InlineKeyboardBuilder()
    for ch in unsubscribed_channels:
        builder.button(text=ch['title'], url=ch['url'])
        
    builder.button(text="🔄 Проверить подписку", callback_data="check_subs")
    builder.adjust(1) # Кнопки друг под другом
    return builder.as_markup()

@user_router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    unsubscribed = await get_unsubscribed_channels(bot, message.from_user.id)
    
    if not unsubscribed:
        # Пользователь подписан на все каналы (или каналов нет в БД)
        await send_main_link(message)
    else:
        # Выводим оставшиеся каналы
        kb = create_subscription_keyboard(unsubscribed)
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Чтобы получить доступ к основному закрытому каналу, пожалуйста, "
            "подпишитесь на наших спонсоров. "
            "После подписки нажмите кнопку <b>Проверить подписку</b>.",
            reply_markup=kb
        )

@user_router.callback_query(F.data == "check_subs")
async def process_check_subs(callback: CallbackQuery, bot: Bot):
    unsubscribed = await get_unsubscribed_channels(bot, callback.from_user.id)
    
    if not unsubscribed:
        # Все подписки оформлены
        await callback.message.delete()
        await send_main_link(callback.message)
    else:
        # Обновляем клавиатуру, оставляя только те каналы, на которые все еще нет подписки
        kb = create_subscription_keyboard(unsubscribed)
        try:
            await callback.message.edit_text(
                "❌ <b>Вы подписались не на все каналы!</b>\n\n"
                "Пожалуйста, подпишитесь на оставшиеся каналы из списка ниже, "
                "чтобы получить доступ:",
                reply_markup=kb
            )
        except TelegramBadRequest:
            # Срабатывает, если текст сообщения не изменился (пользователь нажал кнопку, ничего не сделав)
            pass
            
    await callback.answer()

async def send_main_link(message: Message):
    """Выдает ссылку на основной канал."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Перейти в основной канал", url=MAIN_CHANNEL_INVITE_LINK)
    
    await message.answer(
        "🎉 <b>Спасибо за подписку!</b>\n\n"
        "Ваш доступ открыт. Нажмите на кнопку ниже, чтобы вступить в основной канал.",
        reply_markup=builder.as_markup()
    )



# 7. ЗАПУСК БОТА


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Инициализация базы данных
    init_db()
    
    # Инициализация бота с парсингом HTML
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    # Регистрация роутеров
    dp.include_router(admin_router)
    dp.include_router(user_router)
    
    # Пропуск накопившихся апдейтов (чтобы бот не отвечал на старые сообщения при запуске)
    await bot.delete_webhook(drop_pending_updates=True)
    
    logging.info("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
  

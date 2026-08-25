import asyncio
import logging
import os
import sys
import sqlite3
from datetime import datetime, timedelta
from calendar import monthrange
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aiohttp import web

# ===== НАСТРОЙКИ =====
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
DB_NAME = "bookings.db"
os.makedirs(os.path.dirname(DB_NAME) or ".", exist_ok=True)

# ВСТАВЬТЕ СВОЙ НОВЫЙ ТОКЕН СЮДА:
BOT_TOKEN = "8668305902:AAFCNyqMdfisL-CaSvR1iVxloxHjeDdikeA"

ADMIN_ID = 848204983
TIMEZONE = "Asia/Novosibirsk"
WORK_START, WORK_END, BREAK_TIME = 10, 20, 15
TZ = ZoneInfo(TIMEZONE)

# ===== УСЛУГИ =====
SERVICES = [
    {"name": "Маникюр классический", "duration": 60, "price": 1500},
    {"name": "Маникюр аппаратный", "duration": 60, "price": 1500},
    {"name": "Маникюр комбинированный", "duration": 70, "price": 1700},
    {"name": "Педикюр классический", "duration": 90, "price": 2000},
    {"name": "Педикюр аппаратный", "duration": 90, "price": 2000},
    {"name": "Педикюр комбинированный", "duration": 100, "price": 2200},
    {"name": "Покрытие гель-лак (руки)", "duration": 40, "price": 800},
    {"name": "Покрытие гель-лак (ноги)", "duration": 40, "price": 800},
    {"name": "Дизайн ногтей (1 ноготь)", "duration": 15, "price": 300},
    {"name": "Снятие гель-лака", "duration": 20, "price": 500},
]

# ===== ВРЕМЯ =====
def now_local(): return datetime.now(TZ)
def today_str(): return now_local().date().isoformat()
def format_date(d): return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
def dt_from_booking(d, t): return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
def fmt_money(v): return f"{v:,}".replace(",", " ") + "₽"

# ===== БАЗА ДАННЫХ =====
def db(): return sqlite3.connect(DB_NAME)

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, client_name TEXT, client_contact TEXT, service TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, duration INTEGER NOT NULL, price INTEGER NOT NULL, created_at TEXT NOT NULL, status TEXT DEFAULT 'active', reminder_24_sent INTEGER DEFAULT 0, reminder_2_sent INTEGER DEFAULT 0)")
    conn.commit()
    conn.close()

def add_booking(uid, uname, cname, ccontact, service, d, t, dur, price):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO bookings (user_id,username,client_name,client_contact,service,date,time,duration,price,created_at,status,reminder_24_sent,reminder_2_sent) VALUES (?,?,?,?,?,?,?,?,?,?,'active',0,0)", (uid,uname,cname,ccontact,service,d,t,dur,price,now_local().isoformat()))
    bid = cur.lastrowid
    conn.commit()
    conn.close()
    return bid

def get_booking(bid):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id,user_id,username,client_name,client_contact,service,date,time,duration,price,status FROM bookings WHERE id=?", (bid,))
    return cur.fetchone()

def get_bookings_for_date(d):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT time,duration FROM bookings WHERE date=? AND status='active' ORDER BY time", (d,))
    return cur.fetchall()

def get_all_active_bookings():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id,user_id,username,client_name,client_contact,service,date,time,duration,price FROM bookings WHERE status='active' ORDER BY date,time")
    return cur.fetchall()

def get_user_bookings(uid):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id,service,date,time,duration,price,status FROM bookings WHERE user_id=? ORDER BY date,time", (uid,))
    return cur.fetchall()

def cancel_booking(bid):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (bid,))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0

def reschedule_booking(bid, d, t):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET date=?, time=?, reminder_24_sent=0, reminder_2_sent=0 WHERE id=? AND status='active'", (d,t,bid))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0

def search_clients(q):
    conn = db()
    cur = conn.cursor()
    q = f"%{q}%"
    cur.execute("SELECT id,client_name,client_contact,username,service,date,time,price,status FROM bookings WHERE client_name LIKE ? OR client_contact LIKE ? OR username LIKE ? ORDER BY date DESC,time DESC LIMIT 30", (q,q,q))
    return cur.fetchall()

def get_today_bookings(d):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id,client_name,client_contact,service,time,duration,price,user_id FROM bookings WHERE date=? AND status='active' ORDER BY time", (d,))
    return cur.fetchall()

def get_revenue(s, e):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(price),0) FROM bookings WHERE date BETWEEN ? AND ? AND status='active'", (s,e))
    return cur.fetchone()

def get_reminder_candidates():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id,user_id,client_name,service,date,time,reminder_24_sent,reminder_2_sent FROM bookings WHERE status='active' AND user_id IS NOT NULL")
    return cur.fetchall()

def mark_reminder_sent(bid, kind):
    col = "reminder_24_sent" if kind == "24" else "reminder_2_sent"
    conn = db()
    cur = conn.cursor()
    cur.execute(f"UPDATE bookings SET {col}=1 WHERE id=?", (bid,))
    conn.commit()
    conn.close()

# ===== ПРОВЕРКА ВРЕМЕНИ =====
def is_time_available(d, t, dur):
    if d == today_str() and dt_from_booking(d, t) <= now_local():
        return False
    start = datetime.strptime(t, "%H:%M")
    end = start + timedelta(minutes=dur)
    for st, bd in get_bookings_for_date(d):
        bs = datetime.strptime(st, "%H:%M")
        be = bs + timedelta(minutes=bd + BREAK_TIME)
        if not (end <= bs or start >= be):
            return False
    return True

# ===== FSM =====
class ClientStates(StatesGroup):
    service = State()
    date = State()
    time = State()
    confirm = State()

class AdminStates(StatesGroup):
    client = State()
    service = State()
    date = State()
    time = State()
    confirm = State()

class AdminCancel(StatesGroup):
    booking_id = State()

class AdminReschedule(StatesGroup):
    booking_id = State()
    date = State()
    time = State()

class AdminSearch(StatesGroup):
    query = State()

# ===== КЛАВИАТУРЫ =====
def main_kb(uid):
    b = InlineKeyboardBuilder()
    b.button(text="📅 Записаться", callback_data="client_book")
    b.button(text="📋 Мои записи", callback_data="my_bookings")
    if uid == ADMIN_ID:
        b.button(text="👑 Админ-панель", callback_data="admin_panel")
    b.adjust(1)
    return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    items = [("📅 Сегодня","admin_today"),("📆 Неделя","admin_week"),("➕ Добавить запись","admin_add"),("🔄 Перенести запись","admin_reschedule"),("❌ Отменить запись","admin_cancel"),("🔎 Найти клиента","admin_search"),("💰 Выручка","admin_revenue"),("📋 Все записи","admin_all"),("◀ Главное меню","back_main")]
    for text, data in items:
        b.button(text=text, callback_data=data)
    b.adjust(2,2,2,2,1)
    return b.as_markup()

def services_kb(prefix):
    b = InlineKeyboardBuilder()
    for i,s in enumerate(SERVICES):
        b.button(text=f"{s['name']} — {s['duration']} мин | {fmt_money(s['price'])}", callback_data=f"{prefix}_{i}")
    b.adjust(1)
    return b.as_markup()

def calendar_kb(year, month, prefix):
    b = InlineKeyboardBuilder()
    first = datetime(year, month, 1).date()
    last = datetime(year, month, monthrange(year, month)[1]).date()
    pm = (first - timedelta(days=1)).replace(day=1)
    nm = (last + timedelta(days=1)).replace(day=1)
    b.button(text="◀", callback_data=f"cal:{prefix}:{pm.year}:{pm.month}")
    b.button(text=first.strftime("%m.%Y"), callback_data="ignore")
    b.button(text="▶", callback_data=f"cal:{prefix}:{nm.year}:{nm.month}")
    b.adjust(3)
    for day in ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]:
        b.button(text=day, callback_data="ignore")
    b.adjust(7)
    cur = first
    for _ in range(first.weekday()):
        b.button(text=" ", callback_data="ignore")
    while cur <= last:
        if cur < now_local().date():
            b.button(text="✖", callback_data="ignore")
        else:
            b.button(text=str(cur.day), callback_data=f"{prefix}:{cur.isoformat()}")
        cur += timedelta(days=1)
    b.adjust(7)
    return b.as_markup()

def time_kb(d, dur, prefix):
    b = InlineKeyboardBuilder()
    occupied = []
    for st, bd in get_bookings_for_date(d):
        s = datetime.strptime(st, "%H:%M")
        e = s + timedelta(minutes=bd + BREAK_TIME)
        occupied.append((s, e))
    cur = datetime.strptime(f"{WORK_START}:00", "%H:%M")
    end = datetime.strptime(f"{WORK_END}:00", "%H:%M")
    while cur + timedelta(minutes=dur) <= end:
        slot_end = cur + timedelta(minutes=dur)
        free = True
        for bs, be in occupied:
            if not (slot_end <= bs or cur >= be):
                free = False
                break
        if free:
            b.button(text=cur.strftime("%H:%M"), callback_data=f"{prefix}:{cur.strftime('%H:%M')}")
        cur += timedelta(minutes=30)
    b.adjust(3)
    return b.as_markup()

def confirm_kb(prefix):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"{prefix}:yes")
    b.button(text="❌ Отмена", callback_data=f"{prefix}:no")
    b.adjust(1)
    return b.as_markup()

# ===== ОСНОВНОЙ БОТ =====
logging.info("🚀 Бот D.Cute запускается...")
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
init_db()

async def notify_admin(text):
    try:
        await bot.send_message(ADMIN_ID, text)
    except:
        pass

# === ХЕНДЛЕРЫ ===
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Добро пожаловать в бот записи D.Cute!\n\nВыберите действие:", reply_markup=main_kb(message.from_user.id))

@dp.callback_query(F.data == "client_book")
async def client_book(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("✋ Выберите услугу:", reply_markup=services_kb("client_service"))
    await state.set_state(ClientStates.service)
    await callback.answer()

@dp.callback_query(ClientStates.service, F.data.startswith("client_service:"))
async def client_service(callback: CallbackQuery, state: FSMContext):
    service = SERVICES[int(callback.data.split(":")[1])]
    await state.update_data(service=service)
    today = now_local().date()
    await callback.message.edit_text(f"✋ {service['name']}\n⏱ {service['duration']} мин\n💰 {fmt_money(service['price'])}\n\n📅 Выберите дату:", reply_markup=calendar_kb(today.year, today.month, "client_date"))
    await state.set_state(ClientStates.date)
    await callback.answer()

@dp.callback_query(ClientStates.date, F.data.startswith("client_date:"))
async def client_date(callback: CallbackQuery, state: FSMContext):
    d = callback.data.split(":",1)[1]
    data = await state.get_data()
    service = data["service"]
    await state.update_data(date=d)
    await callback.message.edit_text(f"📅 {format_date(d)}\n✋ {service['name']}\n⏱ {service['duration']} мин\n\n🕐 Выберите свободное время:", reply_markup=time_kb(d, service["duration"], "client_time"))
    await state.set_state(ClientStates.time)
    await callback.answer()

@dp.callback_query(ClientStates.time, F.data.startswith("client_time:"))
async def client_time(callback: CallbackQuery, state: FSMContext):
    t = callback.data.split(":",1)[1]
    data = await state.get_data()
    service = data["service"]
    d = data["date"]
    if not is_time_available(d, t, service["duration"]):
        await callback.answer("Это время уже занято.", show_alert=True)
        return
    await state.update_data(time=t)
    await callback.message.edit_text(f"Проверьте запись:\n\n📅 {format_date(d)}\n🕐 {t}\n✋ {service['name']}\n⏱ {service['duration']} мин\n💰 {fmt_money(service['price'])}\n\nПодтвердить?", reply_markup=confirm_kb("client_confirm"))
    await state.set_state(ClientStates.confirm)
    await callback.answer()

@dp.callback_query(ClientStates.confirm, F.data == "client_confirm:yes")
async def client_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    service = data["service"]
    d = data["date"]
    t = data["time"]
    if not is_time_available(d, t, service["duration"]):
        await callback.answer("Это время уже заняли. Выберите другое.", show_alert=True)
        await state.set_state(ClientStates.time)
        return
    username = callback.from_user.username
    contact = f"@{username}" if username else str(callback.from_user.id)
    bid = add_booking(callback.from_user.id, username, callback.from_user.full_name, contact, service["name"], d, t, service["duration"], service["price"])
    await callback.message.edit_text(f"✅ Запись подтверждена!\n\n📅 {format_date(d)}\n🕐 {t}\n✋ {service['name']}\n⏱ {service['duration']} мин\n💰 {fmt_money(service['price'])}\n\nЯ напомню о записи за 24 часа и за 2 часа.", reply_markup=main_kb(callback.from_user.id))
    await notify_admin(f"📥 <b>Новая запись</b>\n\n👤 {callback.from_user.full_name}\n📱 {contact}\n✋ {service['name']}\n📅 {format_date(d)}\n🕐 {t}\n💰 {fmt_money(service['price'])}\n🆔 Запись #{bid}")
    await state.clear()
    await callback.answer()

@dp.callback_query(ClientStates.confirm, F.data == "client_confirm:no")
async def client_confirm_no(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Запись отменена.", reply_markup=main_kb(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "my_bookings")
async def my_bookings(callback: CallbackQuery):
    rows = get_user_bookings(callback.from_user.id)
    if not rows:
        await callback.message.edit_text("📋 У вас нет записей.", reply_markup=main_kb(callback.from_user.id))
        await callback.answer()
        return
    text = "📋 <b>Мои записи</b>\n\n"
    for row in rows:
        bid, service, d, t, dur, price, status = row
        icon = "✅" if status == "active" else "❌"
        text += f"{icon} <b>#{bid}</b>\n📅 {format_date(d)} | 🕐 {t}\n✋ {service}\n⏱ {dur} мин | 💰 {fmt_money(price)}\n\n"
    await callback.message.edit_text(text, reply_markup=main_kb(callback.from_user.id))
    await callback.answer()

def is_admin(uid):
    return uid == ADMIN_ID

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("👑 <b>Админ-панель</b>\n\nВыберите действие:", reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("👋 Главное меню:", reply_markup=main_kb(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "admin_today")
async def admin_today(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    d = today_str()
    rows = get_today_bookings(d)
    text = f"📅 <b>Сегодня — {format_date(d)}</b>\n\n"
    if not rows:
        text += "Записей нет."
    else:
        for row in rows:
            bid, name, contact, service, t, dur, price, uid = row
            text += f"🕐 <b>{t}</b> — {name or 'Клиент'}\n   ✋ {service}\n   📱 {contact or 'не указан'}\n   💰 {fmt_money(price)} | #{bid}\n\n"
    await callback.message.edit_text(text, reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "admin_week")
async def admin_week(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    start = now_local().date()
    end = start + timedelta(days=6)
    rows = [r for r in get_all_active_bookings() if start.isoformat() <= r[6] <= end.isoformat()]
    text = f"📆 <b>Ближайшие 7 дней</b>\n{format_date(start.isoformat())} — {format_date(end.isoformat())}\n\n"
    if not rows:
        text += "Записей нет."
    else:
        cur_d = None
        for row in rows:
            bid, uid, uname, name, contact, service, d, t, dur, price = row
            if d != cur_d:
                cur_d = d
                text += f"\n<b>📅 {format_date(d)}</b>\n"
            text += f"🕐 {t} — {name or 'Клиент'}\n   {service} | #{bid}\n"
    if len(text) > 3900:
        text = text[:3900] + "\n\n…"
    await callback.message.edit_text(text, reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("➕ <b>Новая запись</b>\n\nВведите клиента в формате:\n\n<code>Анна, +79991234567</code>\n\nили:\n\n<code>Анна, @username</code>")
    await state.set_state(AdminStates.client)
    await callback.answer()

@dp.message(AdminStates.client)
async def admin_client(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if "," in text:
        name, contact = text.split(",",1)
        name = name.strip()
        contact = contact.strip()
    else:
        name = text
        contact = ""
    if not name:
        await message.answer("Введите имя клиента.")
        return
    await state.update_data(client_name=name, client_contact=contact)
    await message.answer(f"👤 <b>{name}</b>\n📱 {contact or 'контакт не указан'}\n\nВыберите услугу:", reply_markup=services_kb("admin_service"))
    await state.set_state(AdminStates.service)

@dp.callback_query(AdminStates.service, F.data.startswith("admin_service:"))
async def admin_service(callback: CallbackQuery, state: FSMContext):
    service = SERVICES[int(callback.data.split(":")[1])]
    await state.update_data(service=service)
    today = now_local().date()
    await callback.message.edit_text(f"👤 {(await state.get_data())['client_name']}\n\n✋ {service['name']}\n⏱ {service['duration']} мин\n💰 {fmt_money(service['price'])}\n\n📅 Выберите дату:", reply_markup=calendar_kb(today.year, today.month, "admin_date"))
    await state.set_state(AdminStates.date)
    await callback.answer()

@dp.callback_query(AdminStates.date, F.data.startswith("admin_date:"))
async def admin_date(callback: CallbackQuery, state: FSMContext):
    d = callback.data.split(":",1)[1]
    data = await state.get_data()
    service = data["service"]
    await state.update_data(date=d)
    await callback.message.edit_text(f"👤 {data['client_name']}\n📱 {data['client_contact'] or 'не указан'}\n\n📅 {format_date(d)}\n✋ {service['name']}\n\n🕐 Выберите время:", reply_markup=time_kb(d, service["duration"], "admin_time"))
    await state.set_state(AdminStates.time)
    await callback.answer()

@dp.callback_query(AdminStates.time, F.data.startswith("admin_time:"))
async def admin_time(callback: CallbackQuery, state: FSMContext):
    t = callback.data.split(":",1)[1]
    data = await state.get_data()
    service = data["service"]
    if not is_time_available(data["date"], t, service["duration"]):
        await callback.answer("Это время уже занято.", show_alert=True)
        return
    await state.update_data(time=t)
    await callback.message.edit_text(f"➕ <b>Проверьте запись</b>\n\n👤 {data['client_name']}\n📱 {data['client_contact'] or 'не указан'}\n📅 {format_date(data['date'])}\n🕐 {t}\n✋ {service['name']}\n⏱ {service['duration']} мин\n💰 {fmt_money(service['price'])}\n\nДобавить?", reply_markup=confirm_kb("admin_confirm"))
    await state.set_state(AdminStates.confirm)
    await callback.answer()

@dp.callback_query(AdminStates.confirm, F.data == "admin_confirm:yes")
async def admin_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    service = data["service"]
    if not is_time_available(data["date"], data["time"], service["duration"]):
        await callback.answer("Время уже занято.", show_alert=True)
        await state.clear()
        return
    bid = add_booking(None, None, data["client_name"], data["client_contact"], service["name"], data["date"], data["time"], service["duration"], service["price"])
    await callback.message.edit_text(f"✅ <b>Запись добавлена</b>\n\n👤 {data['client_name']}\n📱 {data['client_contact'] or 'не указан'}\n📅 {format_date(data['date'])}\n🕐 {data['time']}\n✋ {service['name']}\n💰 {fmt_money(service['price'])}\n🆔 #{bid}", reply_markup=admin_kb())
    await state.clear()
    await callback.answer()

@dp.callback_query(AdminStates.confirm, F.data == "admin_confirm:no")
async def admin_confirm_no(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Добавление отменено.", reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("❌ <b>Отмена записи</b>\n\nВведите ID записи.\n\nНапример: <code>125</code>")
    await state.set_state(AdminCancel.booking_id)
    await callback.answer()

@dp.message(AdminCancel.booking_id)
async def admin_cancel_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Введите только ID записи, например 125.")
        return
    bid = int(text)
    booking = get_booking(bid)
    if not booking or booking[10] != "active":
        await message.answer("Активная запись не найдена.")
        return
    cancel_booking(bid)
    await message.answer(f"❌ Запись #{bid} отменена.", reply_markup=admin_kb())
    if booking[1]:
        try:
            await bot.send_message(booking[1], f"❌ <b>Ваша запись отменена</b>\n\n📅 {format_date(booking[6])}\n🕐 {booking[7]}\n✋ {booking[5]}\n\nЕсли хотите записаться снова — откройте бота.")
        except:
            pass
    await state.clear()

@dp.callback_query(F.data == "admin_reschedule")
async def admin_reschedule(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🔄 <b>Перенос записи</b>\n\nВведите ID записи.")
    await state.set_state(AdminReschedule.booking_id)
    await state.update_data(mode="reschedule")
    await callback.answer()

@dp.message(AdminReschedule.booking_id)
async def admin_booking_id_router(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    if data.get("mode") == "reschedule":
        text = (message.text or "").strip()
        if not text.isdigit():
            await message.answer("Введите ID записи.")
            return
        bid = int(text)
        booking = get_booking(bid)
        if not booking or booking[10] != "active":
            await message.answer("Активная запись не найдена.")
            return
        await state.update_data(booking_id=bid)
        today = now_local().date()
        await message.answer(f"🔄 Перенос записи #{bid}\n\n👤 {booking[3] or 'Клиент'}\n✋ {booking[5]}\n📅 Сейчас: {format_date(booking[6])} {booking[7]}\n\nВыберите новую дату:", reply_markup=calendar_kb(today.year, today.month, "reschedule_date"))
        await state.set_state(AdminReschedule.date)

@dp.callback_query(AdminReschedule.date, F.data.startswith("reschedule_date:"))
async def reschedule_date(callback: CallbackQuery, state: FSMContext):
    d = callback.data.split(":",1)[1]
    data = await state.get_data()
    booking = get_booking(data["booking_id"])
    await state.update_data(date=d)
    await callback.message.edit_text(f"🔄 Запись #{booking[0]}\n\n👤 {booking[3] or 'Клиент'}\n✋ {booking[5]}\n📅 Новая дата: {format_date(d)}\n\nВыберите время:", reply_markup=time_kb(d, booking[8], "reschedule_time"))
    await state.set_state(AdminReschedule.time)
    await callback.answer()

@dp.callback_query(AdminReschedule.time, F.data.startswith("reschedule_time:"))
async def reschedule_time(callback: CallbackQuery, state: FSMContext):
    t = callback.data.split(":",1)[1]
    data = await state.get_data()
    booking = get_booking(data["booking_id"])
    if not is_time_available(data["date"], t, booking[8]):
        await callback.answer("Это время уже занято.", show_alert=True)
        return
    reschedule_booking(booking[0], data["date"], t)
    await callback.message.edit_text(f"✅ <b>Запись перенесена</b>\n\n👤 {booking[3] or 'Клиент'}\n✋ {booking[5]}\n📅 {format_date(data['date'])}\n🕐 {t}", reply_markup=admin_kb())
    if booking[1]:
        try:
            await bot.send_message(booking[1], f"🔄 <b>Ваша запись перенесена</b>\n\n📅 {format_date(data['date'])}\n🕐 {t}\n✋ {booking[5]}")
        except:
            pass
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "admin_search")
async def admin_search(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🔎 <b>Поиск клиента</b>\n\nВведите имя, телефон или @username.")
    await state.set_state(AdminSearch.query)
    await callback.answer()

@dp.message(AdminSearch.query)
async def admin_search_query(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    q = (message.text or "").strip()
    if not q:
        await message.answer("Введите запрос.")
        return
    rows = search_clients(q)
    if not rows:
        await message.answer("🔎 Ничего не найдено.", reply_markup=admin_kb())
        await state.clear()
        return
    text = "🔎 <b>Результаты поиска</b>\n\n"
    for row in rows:
        bid, name, contact, username, service, d, t, price, status = row
        icon = "✅" if status == "active" else "❌"
        text += f"{icon} <b>#{bid}</b> {name or 'Клиент'}\n📱 {contact or username or 'нет'}\n✋ {service}\n📅 {format_date(d)} {t}\n💰 {fmt_money(price)}\n\n"
    if len(text) > 3900:
        text = text[:3900] + "\n\n…"
    await message.answer(text, reply_markup=admin_kb())
    await state.clear()

@dp.callback_query(F.data == "admin_revenue")
async def admin_revenue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    today = now_local().date()
    month_start = today.replace(day=1)
    tc, tt = get_revenue(today.isoformat(), today.isoformat())
    mc, mt = get_revenue(month_start.isoformat(), today.isoformat())
    await callback.message.edit_text(f"💰 <b>Выручка</b>\n\n📅 Сегодня:\nЗаписей: {tc}\nВыручка: <b>{fmt_money(tt)}</b>\n\n📆 С начала месяца:\nЗаписей: {mc}\nВыручка: <b>{fmt_money(mt)}</b>", reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "admin_all")
async def admin_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    rows = get_all_active_bookings()
    if not rows:
        await callback.message.edit_text("📭 Активных записей нет.", reply_markup=admin_kb())
        await callback.answer()
        return
    text = "📋 <b>Активные записи</b>\n\n"
    for row in rows:
        bid, uid, uname, name, contact, service, d, t, dur, price = row
        text += f"<b>#{bid}</b> {name or 'Клиент'}\n📱 {contact or uname or 'нет'}\n📅 {format_date(d)} | 🕐 {t}\n✋ {service}\n💰 {fmt_money(price)}\n\n"
    if len(text) > 3900:
        text = text[:3900] + "\n\n…"
    await callback.message.edit_text(text, reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("cal:"))
async def calendar_nav(callback: CallbackQuery):
    _, prefix, year, month = callback.data.split(":")
    await callback.message.edit_reply_markup(reply_markup=calendar_kb(int(year), int(month), prefix))
    await callback.answer()

@dp.callback_query(F.data == "ignore")
async def ignore(callback: CallbackQuery):
    await callback.answer()

# ===== НАПОМИНАНИЯ =====
async def reminder_loop():
    while True:
        try:
            current = now_local()
            for row in get_reminder_candidates():
                bid, uid, cname, service, d, t, sent24, sent2 = row
                minutes_left = (dt_from_booking(d, t) - current).total_seconds() / 60
                if not sent24 and 1380 <= minutes_left <= 1440:
                    try:
                        await bot.send_message(uid, f"🔔 <b>Напоминание о записи</b>\n\n📅 {format_date(d)}\n🕐 {t}\n✋ {service}\n\nЖдём вас! ❤️")
                        mark_reminder_sent(bid, "24")
                    except:
                        pass
                if not sent2 and 60 <= minutes_left <= 120:
                    try:
                        await bot.send_message(uid, f"⏰ <b>До вашей записи осталось около 2 часов!</b>\n\n📅 {format_date(d)}\n🕐 {t}\n✋ {service}\n\nДо встречи ❤️")
                        mark_reminder_sent(bid, "2")
                    except:
                        pass
        except:
            pass
        await asyncio.sleep(60)

# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER =====
async def health_check(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    logging.info(f"🌐 Веб-сервер запущен на порту {int(os.getenv('PORT', 8080))}")
    await asyncio.Event().wait()

# ===== ЗАПУСК =====
async def main():
    logging.info("🚀 Бот D.Cute запущен")
    logging.info("Часовой пояс: %s", TIMEZONE)
    logging.info("База данных: %s", DB_NAME)
    asyncio.create_task(reminder_loop())
    asyncio.create_task(start_web())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

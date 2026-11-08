import os, re, time, asyncio, html
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command, CommandObject, StateFilter, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton)
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

bot = Bot(os.environ["BOT_TOKEN"], default=DefaultBotProperties(parse_mode="HTML"))
OWNER = int(os.environ["ADMIN_ID"])
db = AsyncIOMotorClient(os.environ["MONGO_URL"]).animebot
dp = Dispatcher()
BOT_USER = ""
PER = 8
TITLES = {"all": "📃 Barcha animelar", "top": "🔥 Mashhur animelar", "new": "🆕 Yangi animelar"}
SORTS = {"all": [("name", 1)], "top": [("views", -1)], "new": [("ts", -1)]}

class New(StatesGroup):
    name = State(); photo = State(); code = State()
    year = State(); lang = State(); genre = State()
class Ad(StatesGroup):
    msg = State()
class Ch(StatesGroup):
    add = State()
class Adm(StatesGroup):
    add = State()

async def is_admin(uid):
    return uid == OWNER or await db.admins.find_one({"_id": uid}) is not None

class AdminF(Filter):
    async def __call__(self, event) -> bool:
        return await is_admin(event.from_user.id)

def num(x):
    return (not x.isdigit(), int(x) if x.isdigit() else 0, x)

def btn(t, d):
    return InlineKeyboardButton(text=t, callback_data=d)

def kb(buttons, row=3):
    b = [btn(t, d) for t, d in buttons]
    return InlineKeyboardMarkup(inline_keyboard=[b[i:i+row] for i in range(0, len(b), row)])

def menu(admin=False):
    rows = [[KeyboardButton(text="🔍 Qidirish"), KeyboardButton(text="📃 Animelar")],
            [KeyboardButton(text="🔥 Mashhur"), KeyboardButton(text="🆕 Yangi")],
            [KeyboardButton(text="ℹ️ Bot haqida")]]
    if admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def share_btn(code):
    return InlineKeyboardButton(text="📤 Ulashish",
        url=f"https://t.me/share/url?url=https://t.me/{BOT_USER}?start={code}")

async def season_kb(code):
    seasons = sorted(await db.videos.distinct("s", {"code": code}), key=num)
    b = [btn(f"📂 {s}-mavsum", f"s:{code}:{s}") for s in seasons]
    rows = [b[i:i+3] for i in range(0, len(b), 3)]
    rows.append([share_btn(code)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def eps_kb(code, s, back):
    eps = sorted(await db.videos.distinct("e", {"code": code, "s": s}), key=num)
    rows = [[btn(e, f"e:{code}:{s}:{e}") for e in eps[i:i+5]] for i in range(0, len(eps), 5)]
    last = [share_btn(code)]
    if back:
        last.insert(0, btn("⬅️ Mavsumlar", f"b:{code}"))
    rows.append(last)
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- majburiy obuna ----------
async def not_joined(uid):
    res = []
    async for ch in db.channels.find():
        try:
            m = await bot.get_chat_member(ch["_id"], uid)
            if m.status in ("left", "kicked"):
                res.append(ch["_id"])
        except Exception:
            pass
    return res

def sub_kb(chs, code):
    rows = [[InlineKeyboardButton(text=f"📢 {c}", url=f"https://t.me/{c[1:]}")] for c in chs]
    rows.append([btn("✅ Tekshirish", f"chk:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

SUB_TEXT = "🔒 <b>Davom etish uchun kanallarga obuna bo'ling:</b>\n\nObuna bo'lgach «✅ Tekshirish» ni bosing."

# ---------- kartochka ----------
async def card(msg, code, uid):
    if not await is_admin(uid):
        nj = await not_joined(uid)
        if nj:
            return await msg.answer(SUB_TEXT, reply_markup=sub_kb(nj, code))
    a = await db.animes.find_one({"_id": code})
    if not a:
        return await msg.answer("😔 <b>Topilmadi.</b>\nNomini yoki kodini tekshiring.")
    seasons = sorted(await db.videos.distinct("s", {"code": code}), key=num)
    if not seasons:
        return await msg.answer("⏳ Hozircha qismlar yo'q.")
    total = await db.videos.count_documents({"code": code})
    text = (f"🎬 <b>{html.escape(a.get('name', code))}</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🎭 <b>Janr:</b> {html.escape(a.get('genre', '-'))}\n"
            f"📅 <b>Yili:</b> {a.get('year', '-')}\n"
            f"🌐 <b>Tili:</b> {html.escape(a.get('lang', '-'))}\n"
            f"🔑 <b>Kodi:</b> <code>{code}</code>\n"
            f"📺 <b>Qismlar:</b> {total} ta\n"
            f"👁 <b>Ko'rilgan:</b> {a.get('views', 0)} marta\n"
            "━━━━━━━━━━━━━━\n"
            "👇 <i>Ko'rish uchun tanlang:</i>")
    markup = await eps_kb(code, seasons[0], False) if len(seasons) == 1 else await season_kb(code)
    if a.get("photo"):
        await msg.answer_photo(a["photo"], caption=text, reply_markup=markup)
    else:
        await msg.answer(text, reply_markup=markup)

async def list_page(kind, page):
    total = await db.animes.count_documents({})
    if not total:
        return "😔 Hozircha animelar yo'q.", None
    pages = (total + PER - 1) // PER
    page = max(0, min(page, pages - 1))
    items = await db.animes.find().sort(SORTS[kind]).skip(page * PER).limit(PER).to_list(PER)
    rows = [[btn("🎬 " + x.get("name", x["_id"]), f"a:{x['_id']}")] for x in items]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"pg:{kind}:{page-1}"))
    nav.append(btn(f"{page+1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(btn("▶️", f"pg:{kind}:{page+1}"))
    rows.append(nav)
    return f"<b>{TITLES[kind]}</b>\nJami: {total} ta", InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- foydalanuvchi ----------
async def welcome(m, uid, name):
    await m.answer(
        "🎌 <b>Anime olamiga xush kelibsiz!</b>\n"
        "Qanday animelarni topib beray?\n\n"
        "🔎 Anime <b>nomini</b> yoki <b>kodini</b> yozing",
        reply_markup=menu(await is_admin(uid)))

@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    await db.users.update_one({"_id": m.from_user.id},
        {"$set": {"name": m.from_user.full_name}, "$setOnInsert": {"ts": time.time()}}, upsert=True)
    if command.args:
        await m.answer("🎌", reply_markup=menu(await is_admin(m.from_user.id)))
        return await card(m, command.args.lower(), m.from_user.id)
    await welcome(m, m.from_user.id, m.from_user.first_name)

@dp.callback_query(F.data.startswith("chk:"))
async def chk(c: CallbackQuery):
    nj = await not_joined(c.from_user.id)
    if nj:
        return await c.answer("❌ Hali hamma kanalga obuna bo'lmadingiz", show_alert=True)
    code = c.data[4:]
    await c.message.delete()
    if code:
        await card(c.message, code, c.from_user.id)
    else:
        await welcome(c.message, c.from_user.id, c.from_user.first_name)
    await c.answer("✅ Rahmat!")

@dp.message(F.text == "🔍 Qidirish", StateFilter(None))
async def m_search(m: Message):
    await m.answer("🔎 Anime <b>nomini</b> yoki <b>kodini</b> yozing 👇")

@dp.message(F.text.in_({"📃 Animelar", "🔥 Mashhur", "🆕 Yangi"}), StateFilter(None))
async def m_list(m: Message):
    kind = {"📃 Animelar": "all", "🔥 Mashhur": "top", "🆕 Yangi": "new"}[m.text]
    t, mk = await list_page(kind, 0)
    await m.answer(t, reply_markup=mk)

@dp.message(F.text == "ℹ️ Bot haqida", StateFilter(None))
async def m_about(m: Message):
    n = await db.animes.count_documents({})
    await m.answer(f"🤖 <b>Anime bot</b>\n━━━━━━━━━━━━━━\n🎬 Animelar: {n} ta\n"
                   "🇺🇿 O'zbek tilida\n⚡️ Tez va qulay\n\n💬 Kod yoki nom yozing — topib beraman!")

@dp.callback_query(F.data.startswith("pg:"))
async def pg(c: CallbackQuery):
    _, kind, page = c.data.split(":")
    t, mk = await list_page(kind, int(page))
    await c.message.edit_text(t, reply_markup=mk)
    await c.answer()

@dp.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()

@dp.callback_query(F.data.startswith("a:"))
async def pick(c: CallbackQuery):
    await card(c.message, c.data[2:], c.from_user.id)
    await c.answer()

@dp.callback_query(F.data.startswith("s:"))
async def season(c: CallbackQuery):
    _, code, s = c.data.split(":")
    await c.message.edit_reply_markup(reply_markup=await eps_kb(code, s, True))
    await c.answer()

@dp.callback_query(F.data.startswith("b:"))
async def back(c: CallbackQuery):
    await c.message.edit_reply_markup(reply_markup=await season_kb(c.data[2:]))
    await c.answer()

@dp.callback_query(F.data.startswith("e:"))
async def episode(c: CallbackQuery):
    _, code, s, e = c.data.split(":")
    v = await db.videos.find_one({"code": code, "s": s, "e": e})
    if not v:
        return await c.answer("Topilmadi", show_alert=True)
    a = await db.animes.find_one({"_id": code}) or {}
    await db.animes.update_one({"_id": code}, {"$inc": {"views": 1}})
    eps = sorted(await db.videos.distinct("e", {"code": code, "s": s}), key=num)
    i = eps.index(e)
    mk = kb([("⏭ Keyingi qism", f"e:{code}:{s}:{eps[i+1]}")], row=1) if i + 1 < len(eps) else None
    await c.message.answer_video(v["fid"], reply_markup=mk,
        caption=f"🎬 <b>{html.escape(a.get('name', code))}</b>\n"
                f"📂 {s}-mavsum • 📺 {e}-qism\n\n🤖 @{BOT_USER}")
    await c.answer()

# ---------- admin ----------
@dp.message(Command("admin"), AdminF())
@dp.message(F.text == "🛠 Admin panel", AdminF())
async def admin(m: Message):
    await m.answer("🛠 <b>Admin panel</b>", reply_markup=kb([
        ("📊 Statistika", "adm:stat"), ("📢 Reklama", "adm:ad"),
        ("🎬 Anime qo'shish", "adm:new"), ("📺 Kanallar", "adm:ch"),
        ("👮 Adminlar", "adm:adm"), ("🤖 Bot holati", "adm:bot")], row=2))

@dp.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ Bekor qilindi.")

@dp.callback_query(F.data.startswith("adm:"), AdminF())
async def adm(c: CallbackQuery, state: FSMContext):
    act = c.data[4:]
    owner = c.from_user.id == OWNER
    if act == "stat":
        agg = await db.animes.aggregate([{"$group": {"_id": None, "v": {"$sum": "$views"}}}]).to_list(1)
        await c.message.answer(
            "📊 <b>Statistika</b>\n━━━━━━━━━━━━━━\n"
            f"👥 Foydalanuvchilar: {await db.users.count_documents({})}\n"
            f"🆕 Oxirgi 24 soatda: {await db.users.count_documents({'ts': {'$gte': time.time() - 86400}})}\n"
            f"🎬 Animelar: {await db.animes.count_documents({})}\n"
            f"📺 Qismlar: {await db.videos.count_documents({})}\n"
            f"👁 Ko'rishlar: {agg[0]['v'] if agg else 0}")
    elif act == "new":
        await state.set_state(New.name)
        await c.message.answer("🎬 Anime <b>nomini</b> yozing:\n<i>(bekor qilish: /cancel)</i>")
    elif act == "ad":
        await state.set_state(Ad.msg)
        await c.message.answer("📢 Reklama xabarini yuboring (matn, rasm yoki video).\n<i>Bekor qilish: /cancel</i>")
    elif act == "bot":
        await db.command("ping")
        await c.message.answer("🤖 Bot ishlayapti ✅\n🗄 Baza ulangan ✅")
    elif act == "ch":
        items = [x async for x in db.channels.find()]
        rows = [[btn(f"❌ {x['_id']}", f"adm:rmch:{x['_id']}")] for x in items]
        rows.append([btn("➕ Kanal qo'shish", "adm:addch")])
        await c.message.answer("📺 <b>Majburiy obuna kanallari</b>\n" + ("" if items else "Hozircha yo'q.") +
            "\n<i>Bot kanalda admin bo'lishi shart.</i>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif act == "addch":
        await state.set_state(Ch.add)
        await c.message.answer("Kanal username'ini yozing (masalan @kanalim).\nBotni avval kanalga <b>admin</b> qiling.")
    elif act.startswith("rmch:"):
        await db.channels.delete_one({"_id": act[5:]})
        await c.message.answer("🗑 Kanal o'chirildi.")
    elif act == "adm":
        if not owner:
            return await c.answer("Faqat asosiy admin uchun", show_alert=True)
        items = [x async for x in db.admins.find()]
        rows = [[btn(f"❌ {x['_id']}", f"adm:rmadm:{x['_id']}")] for x in items]
        rows.append([btn("➕ Admin qo'shish", "adm:addadm")])
        await c.message.answer("👮 <b>Adminlar</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif act == "addadm":
        if not owner:
            return await c.answer("Faqat asosiy admin uchun", show_alert=True)
        await state.set_state(Adm.add)
        await c.message.answer("Yangi adminning Telegram <b>ID raqamini</b> yozing:")
    elif act.startswith("rmadm:"):
        if not owner:
            return await c.answer("Faqat asosiy admin uchun", show_alert=True)
        await db.admins.delete_one({"_id": int(act[6:])})
        await c.message.answer("🗑 Admin o'chirildi.")
    await c.answer()

@dp.message(Ch.add, F.text, AdminF())
async def ch_add(m: Message, state: FSMContext):
    await state.clear()
    x = "@" + m.text.strip().lstrip("@")
    try:
        await bot.get_chat(x)
        me = await bot.get_chat_member(x, bot.id)
        if me.status != "administrator":
            raise ValueError
    except Exception:
        return await m.answer("❌ Kanal topilmadi yoki bot unda admin emas.")
    await db.channels.update_one({"_id": x}, {"$set": {"on": 1}}, upsert=True)
    await m.answer(f"✅ {x} qo'shildi.")

@dp.message(Adm.add, F.text, AdminF())
async def adm_add(m: Message, state: FSMContext):
    await state.clear()
    if not m.text.strip().isdigit():
        return await m.answer("❌ Faqat raqam yozing.")
    await db.admins.update_one({"_id": int(m.text.strip())}, {"$set": {"on": 1}}, upsert=True)
    await m.answer("✅ Admin qo'shildi.")

@dp.message(New.name, F.text, AdminF())
async def n1(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(New.photo)
    await m.answer("🖼 Poster <b>rasmini</b> yuboring:")

@dp.message(New.photo, F.photo, AdminF())
async def n2(m: Message, state: FSMContext):
    await state.update_data(photo=m.photo[-1].file_id)
    await state.set_state(New.code)
    await m.answer("🔑 Anime <b>kodini</b> yozing (lotin harf/raqam, probelsiz).\n"
                   "Eski animeni yangilash uchun o'sha kodni yozing.")

@dp.message(New.code, F.text, AdminF())
async def n3(m: Message, state: FSMContext):
    await state.update_data(code=m.text.strip().lower().replace(" ", ""))
    await state.set_state(New.year)
    await m.answer("📅 <b>Yilini</b> yozing (masalan 2026):")

@dp.message(New.year, F.text, AdminF())
async def n4(m: Message, state: FSMContext):
    await state.update_data(year=m.text.strip())
    await state.set_state(New.lang)
    await m.answer("🌐 <b>Tilini</b> yozing (masalan O'zbek tilida):")

@dp.message(New.lang, F.text, AdminF())
async def n5(m: Message, state: FSMContext):
    await state.update_data(lang=m.text.strip())
    await state.set_state(New.genre)
    await m.answer("🎭 <b>Janrlarni</b> probel bilan yozing (masalan: komediya dahshat sarguzasht):")

@dp.message(New.genre, F.text, AdminF())
async def n6(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    genre = " ".join("#" + w.lstrip("#") for w in m.text.split())
    await db.animes.update_one({"_id": d["code"]},
        {"$set": {"name": d["name"], "photo": d["photo"], "year": d["year"],
                  "lang": d["lang"], "genre": genre},
         "$setOnInsert": {"ts": time.time()}}, upsert=True)
    await m.answer(f"✅ Saqlandi: <b>{html.escape(d['name'])}</b>\n\n"
                   "Endi videolarni skrepka orqali yuboring, izohiga:\n"
                   f"<code>{d['code']} 1 1</code>  (kod, mavsum, qism)")

@dp.message(Ad.msg, AdminF())
async def send_ad(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("📢 Yuborilmoqda...")
    ok = bad = 0
    async for u in db.users.find():
        try:
            await bot.copy_message(u["_id"], m.chat.id, m.message_id)
            ok += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
    await m.answer(f"📢 <b>Tugadi</b>\n✅ Yuborildi: {ok}\n❌ Yetmadi: {bad}")

@dp.message(Command("nom"), AdminF())
async def nom(m: Message):
    p = m.text.split(maxsplit=2)
    if len(p) < 3:
        return await m.answer("Yozing: /nom kod Anime nomi")
    await db.animes.update_one({"_id": p[1].lower()}, {"$set": {"name": p[2]}}, upsert=True)
    await m.answer("✅ Nomi saqlandi.")

@dp.message(Command("del"), AdminF())
async def delete(m: Message):
    p = m.text.split()
    if len(p) != 2:
        return await m.answer("Yozing: /del kod")
    code = p[1].lower()
    await db.animes.delete_one({"_id": code})
    r = await db.videos.delete_many({"code": code})
    await m.answer(f"🗑 O'chirildi: {code} ({r.deleted_count} qism)")

@dp.message(F.video, AdminF(), StateFilter(None))
async def add(m: Message):
    p = (m.caption or "").split()
    if len(p) != 3:
        return await m.answer("Izoh: kod mavsum qism\nMasalan: kokzindon 1 1")
    code, s, e = p[0].lower(), p[1], p[2]
    await db.animes.update_one({"_id": code},
        {"$setOnInsert": {"name": code, "ts": time.time()}}, upsert=True)
    await db.videos.update_one({"code": code, "s": s, "e": e},
                               {"$set": {"fid": m.video.file_id}}, upsert=True)
    await m.answer(f"✅ Saqlandi: {code} • {s}-mavsum • {e}-qism")

@dp.message(F.text, StateFilter(None))
async def text(m: Message):
    q = m.text.strip().lower()
    if await db.animes.find_one({"_id": q}):
        return await card(m, q, m.from_user.id)
    found = await db.animes.find({"name": {"$regex": re.escape(q), "$options": "i"}}).limit(10).to_list(10)
    if len(found) == 1:
        return await card(m, found[0]["_id"], m.from_user.id)
    if found:
        return await m.answer("🔎 <b>Topilgan animelar:</b>", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[btn("🎬 " + x.get("name", x["_id"]), f"a:{x['_id']}")] for x in found]))
    await m.answer("😔 <b>Topilmadi.</b>\nNomini yoki kodini tekshiring, yoki 📃 Animelar bo'limini oching.")

async def main():
    global BOT_USER
    BOT_USER = (await bot.get_me()).username
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await dp.start_polling(bot)

asyncio.run(main())

import os, re, time, asyncio, html, logging
from urllib.parse import quote
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.filters import CommandStart, Command, CommandObject, StateFilter, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, ErrorEvent, InlineKeyboardMarkup,
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile)
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

logging.basicConfig(level=logging.INFO)
bot = Bot(os.environ["BOT_TOKEN"], default=DefaultBotProperties(parse_mode="HTML"))
OWNER = int(os.environ["ADMIN_ID"])
db = AsyncIOMotorClient(os.environ["MONGO_URL"]).animebot
dp = Dispatcher()
BOT_USER = ""
START = time.time()
PER = 8
PERP = 20
AD = {}
LOCKS = {}
CACHE = {}
TITLES = {"all": "📃 Barcha animelar", "top": "🔥 Mashhur animelar", "new": "🆕 Yangi animelar"}
SORTS = {"all": [("name", 1)], "top": [("views", -1)], "new": [("ts", -1)]}
LANGS = {"uz": "O'zbek tilida", "ru": "Rus tilida", "en": "Ingliz tilida", "sub": "Original (subtitr)"}
WELCOME = ("🎌 <b>Anime olamiga xush kelibsiz!</b>\n"
           "Qanday animelarni topib beray?\n\n"
           "🔎 Anime <b>nomini</b> yoki <b>kodini</b> yozing")
SUB_TEXT = ("🔒 <b>Davom etish uchun kanallarga obuna bo'ling:</b>\n\n"
            "Obuna bo'lgach «✅ Tekshirish» ni bosing.")

# ================= holatlar =================
class New(StatesGroup):
    name = State(); photo = State(); code = State()
    year = State(); lang = State(); genre = State()
class Up(StatesGroup):
    season = State(); files = State()
class Ad(StatesGroup):
    msg = State(); btn = State(); confirm = State()
class Bn(StatesGroup):
    text = State(); btn = State()
class Ch(StatesGroup):
    add = State()
class Adm(StatesGroup):
    add = State()

# ================= yordamchilar =================
async def is_admin(uid):
    return uid == OWNER or await db.admins.find_one({"_id": uid}) is not None

class AdminF(Filter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and await is_admin(event.from_user.id)

class Track(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = event.from_user
        if u and not u.is_bot:
            await db.users.update_one({"_id": u.id},
                {"$set": {"name": u.full_name, "last": time.time(), "dead": False},
                 "$setOnInsert": {"ts": time.time()}}, upsert=True)
        return await handler(event, data)

dp.message.outer_middleware(Track())
dp.callback_query.outer_middleware(Track())

def esc(x):
    return html.escape(str(x))

def clean_code(t):
    t = t.strip().lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", t)[:30]

def btn(t, d):
    return InlineKeyboardButton(text=t, callback_data=d)

def kb(buttons, row=2):
    b = [btn(t, d) for t, d in buttons]
    return InlineKeyboardMarkup(inline_keyboard=[b[i:i+row] for i in range(0, len(b), row)])

def parse_btn(t):
    if "|" not in t:
        return None
    a, b = [x.strip() for x in t.split("|", 1)]
    if b.startswith("t.me/"):
        b = "https://" + b
    if not a or not b.startswith(("http://", "https://")):
        return None
    return a, b

def menu(admin=False):
    rows = [[KeyboardButton(text="🔍 Qidirish"), KeyboardButton(text="📃 Animelar")],
            [KeyboardButton(text="🔥 Mashhur"), KeyboardButton(text="🆕 Yangi")],
            [KeyboardButton(text="ℹ️ Bot haqida")]]
    if admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def panel():
    return kb([("📊 Statistika", "adm:stat"), ("🎬 Anime qo'shish", "adm:new"),
               ("🎞 Animelarni boshqarish", "adm:list"), ("📢 Reklama yuborish", "adm:ad"),
               ("📌 Reklama banner", "adm:bn"), ("📺 Kanallar", "adm:ch"),
               ("👮 Adminlar", "adm:adm"), ("🤖 Bot holati", "adm:bot")], row=2)

def nav_row(prefix, page, pages):
    r = []
    if page > 0:
        r.append(btn("◀️", f"{prefix}:{page-1}"))
    r.append(btn(f"{page+1}/{pages}", "noop"))
    if page < pages - 1:
        r.append(btn("▶️", f"{prefix}:{page+1}"))
    return r

async def load_ad():
    global AD
    AD = await db.settings.find_one({"_id": "ad"}) or {}

def ad_row():
    if AD.get("btn_text") and AD.get("btn_url"):
        return [InlineKeyboardButton(text=AD["btn_text"], url=AD["btn_url"])]
    return None

def share_btn(code):
    link = quote(f"https://t.me/{BOT_USER}?start={code}", safe="")
    return InlineKeyboardButton(text="📤 Ulashish", url=f"https://t.me/share/url?url={link}")

async def safe_edit_markup(msg, markup):
    try:
        await msg.edit_reply_markup(reply_markup=markup)
    except Exception:
        pass

# ================= majburiy obuna =================
async def not_joined(uid, fresh=False):
    out = []
    async for ch in db.chans.find():
        key = (uid, ch["_id"])
        hit = CACHE.get(key)
        if hit and not fresh and time.time() - hit[0] < 60:
            joined = hit[1]
        else:
            try:
                m = await bot.get_chat_member(ch["_id"], uid)
                joined = m.status not in ("left", "kicked")
            except Exception:
                joined = True
            CACHE[key] = (time.time(), joined)
        if not joined:
            out.append(ch)
    return out

async def gate(msg, uid, code=""):
    if await is_admin(uid):
        return False
    nj = await not_joined(uid)
    if not nj:
        return False
    rows = [[InlineKeyboardButton(text=f"📢 {c.get('title', 'Kanal')}", url=c["link"])]
            for c in nj if c.get("link")]
    rows.append([btn("✅ Tekshirish", f"chk:{code}")])
    await msg.answer(SUB_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    return True

# ================= kartochka =================
async def seasons_kb(code):
    seasons = sorted(await db.episodes.distinct("s", {"code": code}))
    b = [btn(f"📂 {s}-mavsum", f"s:{code}:{s}:0") for s in seasons]
    rows = [b[i:i+2] for i in range(0, len(b), 2)]
    rows.append([share_btn(code)])
    if ad_row():
        rows.append(ad_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def eps_kb(code, s, page, multi):
    eps = sorted(await db.episodes.distinct("e", {"code": code, "s": s}))
    pages = max(1, (len(eps) + PERP - 1) // PERP)
    page = max(0, min(page, pages - 1))
    chunk = eps[page * PERP:(page + 1) * PERP]
    rows = [[btn(str(e), f"e:{code}:{s}:{e}") for e in chunk[i:i+5]]
            for i in range(0, len(chunk), 5)]
    if pages > 1:
        rows.append(nav_row(f"s:{code}:{s}", page, pages))
    last = []
    if multi:
        last.append(btn("⬅️ Mavsumlar", f"b:{code}"))
    last.append(share_btn(code))
    rows.append(last)
    if ad_row():
        rows.append(ad_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_card(msg, code, uid):
    if await gate(msg, uid, code):
        return
    a = await db.titles.find_one({"_id": code})
    if not a:
        return await msg.answer("😔 <b>Topilmadi.</b>\nNomini yoki kodini tekshiring.")
    seasons = sorted(await db.episodes.distinct("s", {"code": code}))
    if not seasons:
        return await msg.answer("⏳ Bu animega hozircha qismlar qo'shilmagan.")
    total = await db.episodes.count_documents({"code": code})
    q = lambda x: f"<blockquote>{x}</blockquote>"
    text = (q(f"🎬 <b>{esc(a.get('name', code))}</b>")
            + "\n— — — — — — — — —\n"
            + q(f"🎭 <b>Janr:</b> {esc(a.get('genre') or '-')}")
            + q(f"📅 <b>Yili:</b> {esc(a.get('year') or '-')}")
            + q(f"🌐 <b>Tili:</b> {esc(a.get('lang') or '-')}")
            + q(f"🎬 <b>Anime kodi:</b> «<code>{code}</code>»")
            + q(f"📺 <b>Qismlar:</b> {total} ta")
            + q(f"📥 <b>Yuklangan:</b> {a.get('views', 0)} marta")
            + "\n👇 <i>Ko'rish uchun qismni tanlang:</i>")
    if AD.get("text"):
        text += f"\n\n📢 {esc(AD['text'])}"
    markup = (await eps_kb(code, seasons[0], 0, False) if len(seasons) == 1
              else await seasons_kb(code))
    if a.get("photo"):
        try:
            return await msg.answer_photo(a["photo"], caption=text, reply_markup=markup)
        except Exception:
            pass
    await msg.answer(text, reply_markup=markup)

async def send_ep(msg, ep, uid):
    code, s, e = ep["code"], ep["s"], ep["e"]
    a = await db.titles.find_one({"_id": code}) or {}
    await db.titles.update_one({"_id": code}, {"$inc": {"views": 1}})
    eps = sorted(await db.episodes.distinct("e", {"code": code, "s": s}))
    nxt = next((x for x in eps if x > e), None)
    rows = []
    if nxt:
        rows.append([btn("⏭ Keyingi qism", f"e:{code}:{s}:{nxt}")])
    rows.append([share_btn(code)])
    if ad_row():
        rows.append(ad_row())
    cap = (f"🎬 <b>{esc(a.get('name', code))}</b>\n"
           f"📂 {s}-mavsum • 📺 {e}-qism\n")
    if AD.get("text"):
        cap += f"\n📢 {esc(AD['text'])}\n"
    cap += f"\n🤖 @{BOT_USER}"
    mk = InlineKeyboardMarkup(inline_keyboard=rows)
    if ep.get("kind") == "document":
        await msg.answer_document(ep["fid"], caption=cap, reply_markup=mk)
    else:
        await msg.answer_video(ep["fid"], caption=cap, reply_markup=mk)

async def list_page(kind, page):
    total = await db.titles.count_documents({})
    if not total:
        return "😔 Hozircha animelar yo'q.", None
    pages = (total + PER - 1) // PER
    page = max(0, min(page, pages - 1))
    items = await db.titles.find().sort(SORTS[kind]).skip(page * PER).limit(PER).to_list(PER)
    rows = [[btn("🎬 " + x.get("name", x["_id"]), f"a:{x['_id']}")] for x in items]
    rows.append(nav_row(f"pg:{kind}", page, pages))
    return f"<b>{TITLES[kind]}</b>\nJami: {total} ta", InlineKeyboardMarkup(inline_keyboard=rows)

# ================= xatolar =================
@dp.errors()
async def on_error(event: ErrorEvent):
    logging.exception(event.exception)
    try:
        await bot.send_message(OWNER, "⚠️ <b>Bot xatosi:</b>\n<code>"
                               + esc(repr(event.exception)[:400]) + "</code>")
    except Exception:
        pass
    return True

# ================= bekor qilish =================
@dp.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ Bekor qilindi.")

# ================= foydalanuvchi =================
@dp.message(CommandStart())
async def start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    adm = await is_admin(m.from_user.id)
    if command.args:
        await m.answer("🎌", reply_markup=menu(adm))
        return await show_card(m, clean_code(command.args), m.from_user.id)
    await m.answer(WELCOME, reply_markup=menu(adm))
    if AD.get("text"):
        rows = [ad_row()] if ad_row() else []
        await m.answer(f"📢 {esc(AD['text'])}",
                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@dp.callback_query(F.data.startswith("chk:"))
async def chk(c: CallbackQuery):
    if await not_joined(c.from_user.id, True):
        return await c.answer("❌ Hali hamma kanalga obuna bo'lmadingiz", show_alert=True)
    code = c.data[4:]
    try:
        await c.message.delete()
    except Exception:
        pass
    if code:
        await show_card(c.message, code, c.from_user.id)
    else:
        await c.message.answer(WELCOME)
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
    n = await db.titles.count_documents({})
    k = await db.episodes.count_documents({})
    await m.answer("🤖 <b>Anime bot</b>\n━━━━━━━━━━━━━━\n"
                   f"🎬 Animelar: {n} ta\n📺 Qismlar: {k} ta\n"
                   "🇺🇿 O'zbek tilida\n⚡️ Tez va qulay\n\n"
                   "💬 Kod yoki nom yozing — topib beraman!")

@dp.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()

@dp.callback_query(F.data.startswith("pg:"))
async def pg(c: CallbackQuery):
    _, kind, page = c.data.split(":")
    t, mk = await list_page(kind, int(page))
    try:
        await c.message.edit_text(t, reply_markup=mk)
    except Exception:
        pass
    await c.answer()

@dp.callback_query(F.data.startswith("a:"))
async def pick(c: CallbackQuery):
    await show_card(c.message, c.data[2:], c.from_user.id)
    await c.answer()

@dp.callback_query(F.data.startswith("s:"))
async def season(c: CallbackQuery):
    _, code, s, p = c.data.split(":")
    await safe_edit_markup(c.message, await eps_kb(code, int(s), int(p), True))
    await c.answer()

@dp.callback_query(F.data.startswith("b:"))
async def back(c: CallbackQuery):
    await safe_edit_markup(c.message, await seasons_kb(c.data[2:]))
    await c.answer()

@dp.callback_query(F.data.startswith("e:"))
async def episode(c: CallbackQuery):
    _, code, s, e = c.data.split(":")
    if await gate(c.message, c.from_user.id, code):
        return await c.answer()
    ep = await db.episodes.find_one({"code": code, "s": int(s), "e": int(e)})
    if not ep:
        return await c.answer("Qism topilmadi", show_alert=True)
    await send_ep(c.message, ep, c.from_user.id)
    await c.answer()

# ================= admin panel =================
@dp.message(Command("admin"), AdminF())
@dp.message(F.text == "🛠 Admin panel", AdminF())
async def admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 <b>Admin panel</b>", reply_markup=panel())

@dp.callback_query(F.data.startswith("adm:"), AdminF())
async def adm(c: CallbackQuery, state: FSMContext):
    act = c.data[4:]
    owner = c.from_user.id == OWNER
    await c.answer()
    if act == "stat":
        agg = await db.titles.aggregate(
            [{"$group": {"_id": None, "v": {"$sum": "$views"}}}]).to_list(1)
        now = time.time()
        await c.message.answer(
            "📊 <b>Statistika</b>\n━━━━━━━━━━━━━━\n"
            f"👥 Foydalanuvchilar: {await db.users.count_documents({})}\n"
            f"🆕 Bugun qo'shilgan: {await db.users.count_documents({'ts': {'$gte': now - 86400}})}\n"
            f"🔥 Bugun faol: {await db.users.count_documents({'last': {'$gte': now - 86400}})}\n"
            f"🚫 Botni bloklagan: {await db.users.count_documents({'dead': True})}\n"
            f"🎬 Animelar: {await db.titles.count_documents({})}\n"
            f"📺 Qismlar: {await db.episodes.count_documents({})}\n"
            f"👁 Ko'rishlar: {agg[0]['v'] if agg else 0}\n"
            f"📺 Majburiy kanallar: {await db.chans.count_documents({})}")
    elif act == "new":
        await state.clear()
        await state.set_state(New.name)
        await c.message.answer("🎬 Anime <b>nomini</b> yozing:\n<i>(bekor qilish: /cancel)</i>")
    elif act == "list":
        t, mk = await manage_page(0)
        await c.message.answer(t, reply_markup=mk)
    elif act == "ad":
        await state.clear()
        await state.set_state(Ad.msg)
        await c.message.answer("📢 Reklama xabarini yuboring (matn, rasm yoki video).\n"
                               "<i>Bekor qilish: /cancel</i>")
    elif act == "bn":
        await state.clear()
        await state.set_state(Bn.text)
        cur = AD.get("text") or "yo'q"
        await c.message.answer(f"📌 <b>Reklama banner</b>\nHozirgi matn: {esc(cur)}\n\n"
                               "Yangi banner matnini yozing.\n"
                               "/skip — matnsiz, /off — bannerni butunlay o'chirish")
    elif act == "bot":
        await db.command("ping")
        up = int(time.time() - START)
        await c.message.answer("🤖 Bot ishlayapti ✅\n🗄 Baza ulangan ✅\n"
                               f"⏱ Ishlash vaqti: {up // 3600} soat {up % 3600 // 60} daqiqa")
    elif act == "ch":
        items = [x async for x in db.chans.find()]
        rows = [[btn(f"❌ {x.get('title', x['_id'])}", f"rc:{x['_id']}")] for x in items]
        rows.append([btn("➕ Kanal qo'shish", "adm:addch")])
        await c.message.answer("📺 <b>Majburiy obuna kanallari</b>\n"
                               + ("" if items else "Hozircha yo'q.\n")
                               + "<i>Bot kanalda admin bo'lishi shart.</i>",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif act == "addch":
        await state.set_state(Ch.add)
        await c.message.answer("📺 Botni avval kanalga <b>admin</b> qiling. Keyin:\n"
                               "• kanaldan istalgan xabarni shu yerga <b>forward</b> qiling, yoki\n"
                               "• <code>@kanalim</code> yozing, yoki\n"
                               "• kanal ID sini (<code>-100...</code>) yozing.\n"
                               "<i>Bekor: /cancel</i>")
    elif act == "adm":
        if not owner:
            return await c.message.answer("Faqat asosiy admin uchun.")
        items = [x async for x in db.admins.find()]
        rows = [[btn(f"❌ {x['_id']}", f"ra:{x['_id']}")] for x in items]
        rows.append([btn("➕ Admin qo'shish", "adm:addadm")])
        await c.message.answer("👮 <b>Adminlar</b>\n" + ("" if items else "Qo'shimcha admin yo'q."),
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif act == "addadm":
        if not owner:
            return await c.message.answer("Faqat asosiy admin uchun.")
        await state.set_state(Adm.add)
        await c.message.answer("Yangi adminning Telegram <b>ID raqamini</b> yozing.\n"
                               "(ID ni @getmyid_bot dan olish mumkin)")

@dp.callback_query(F.data.startswith("rc:"), AdminF())
async def rm_ch(c: CallbackQuery):
    await db.chans.delete_one({"_id": int(c.data[3:])})
    await c.message.answer("🗑 Kanal o'chirildi.")
    await c.answer()

@dp.callback_query(F.data.startswith("ra:"), AdminF())
async def rm_adm(c: CallbackQuery):
    if c.from_user.id != OWNER:
        return await c.answer("Faqat asosiy admin", show_alert=True)
    await db.admins.delete_one({"_id": int(c.data[3:])})
    await c.message.answer("🗑 Admin o'chirildi.")
    await c.answer()

# ---------- kanal / admin qo'shish ----------
@dp.message(Ch.add)
async def ch_add(m: Message, state: FSMContext):
    chat = getattr(m.forward_origin, "chat", None) if m.forward_origin else None
    try:
        if chat is None:
            t = (m.text or "").strip()
            if not t:
                return await m.answer("❌ Forward qiling yoki @username / ID yozing.")
            x = int(t) if re.fullmatch(r"-?\d+", t) else "@" + t.lstrip("@")
            chat = await bot.get_chat(x)
        me = await bot.get_chat_member(chat.id, bot.id)
        if me.status not in ("administrator", "creator"):
            return await m.answer("❌ Bot bu kanalda admin emas. Avval admin qiling.")
        link = f"https://t.me/{chat.username}" if chat.username else None
        if not link:
            try:
                link = (await bot.create_chat_invite_link(chat.id)).invite_link
            except Exception:
                link = None
        if not link:
            return await m.answer("❌ Kanal havolasini olib bo'lmadi. "
                                  "Botga «Havola orqali taklif qilish» huquqini bering.")
        await db.chans.update_one({"_id": chat.id},
            {"$set": {"title": chat.title or str(chat.id), "link": link}}, upsert=True)
        await state.clear()
        await m.answer(f"✅ Qo'shildi: <b>{esc(chat.title)}</b>")
    except Exception as e:
        await m.answer(f"❌ Xato: <code>{esc(str(e)[:200])}</code>\n"
                       "Kanal topilmadi yoki bot admin emas.")

@dp.message(Adm.add, F.text)
async def adm_add(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit():
        return await m.answer("❌ Faqat raqam yozing (yoki /cancel).")
    await state.clear()
    await db.admins.update_one({"_id": int(t)}, {"$set": {"on": 1}}, upsert=True)
    await m.answer("✅ Admin qo'shildi.")

# ---------- banner ----------
@dp.message(Bn.text, F.text)
async def bn_text(m: Message, state: FSMContext):
    t = m.text.strip()
    if t == "/off":
        await db.settings.delete_one({"_id": "ad"})
        await load_ad()
        await state.clear()
        return await m.answer("🗑 Banner o'chirildi.")
    await state.update_data(text="" if t == "/skip" else t)
    await state.set_state(Bn.btn)
    await m.answer("🔗 Tugma qo'shasizmi? Shunday yozing:\n"
                   "<code>Kanalga o'tish | https://t.me/kanalim</code>\n"
                   "Tugmasiz bo'lsa /skip")

@dp.message(Bn.btn, F.text)
async def bn_btn(m: Message, state: FSMContext):
    d = await state.get_data()
    b = None if m.text.strip() == "/skip" else parse_btn(m.text)
    if m.text.strip() != "/skip" and not b:
        return await m.answer("❌ Format noto'g'ri. Misol:\n"
                              "<code>Kanalga o'tish | https://t.me/kanalim</code>")
    await state.clear()
    await db.settings.update_one({"_id": "ad"}, {"$set": {
        "text": d.get("text", ""),
        "btn_text": b[0] if b else "", "btn_url": b[1] if b else ""}}, upsert=True)
    await load_ad()
    await m.answer("✅ Banner saqlandi. Endi u anime kartochkalari, videolar va /start tagida chiqadi.")

# ---------- reklama yuborish ----------
@dp.message(Ad.msg)
async def ad_msg(m: Message, state: FSMContext):
    await state.update_data(src=m.message_id)
    await state.set_state(Ad.btn)
    await m.answer("🔗 Tugma qo'shasizmi? Shunday yozing:\n"
                   "<code>Ko'rish | https://t.me/kanalim</code>\nTugmasiz bo'lsa /skip")

@dp.message(Ad.btn, F.text)
async def ad_btn(m: Message, state: FSMContext):
    d = await state.get_data()
    b = None if m.text.strip() == "/skip" else parse_btn(m.text)
    if m.text.strip() != "/skip" and not b:
        return await m.answer("❌ Format noto'g'ri. Misol:\n<code>Ko'rish | https://t.me/kanalim</code>")
    await state.update_data(b=list(b) if b else None)
    mk = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b[0], url=b[1])]])
          if b else None)
    await m.answer("👀 <b>Ko'rinishi:</b>")
    await bot.copy_message(m.chat.id, m.chat.id, d["src"], reply_markup=mk)
    await state.set_state(Ad.confirm)
    await m.answer("Hammaga yuboraymi?", reply_markup=kb(
        [("✅ Yuborish", "ad:go"), ("✖️ Bekor", "ad:no")], row=2))

async def broadcast(chat_id, src, mk):
    ok = bad = 0
    await bot.send_message(chat_id, "📢 Yuborilmoqda...")
    async for u in db.users.find({"dead": {"$ne": True}}):
        try:
            await bot.copy_message(u["_id"], chat_id, src, reply_markup=mk)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(u["_id"], chat_id, src, reply_markup=mk)
                ok += 1
            except Exception:
                bad += 1
        except TelegramForbiddenError:
            bad += 1
            await db.users.update_one({"_id": u["_id"]}, {"$set": {"dead": True}})
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
    await bot.send_message(chat_id, f"📢 <b>Tugadi</b>\n✅ Yuborildi: {ok}\n❌ Yetmadi: {bad}")

@dp.callback_query(Ad.confirm, F.data.in_({"ad:go", "ad:no"}))
async def ad_confirm(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await c.answer()
    if c.data == "ad:no":
        return await c.message.answer("✖️ Bekor qilindi.")
    b = d.get("b")
    mk = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b[0], url=b[1])]])
          if b else None)
    asyncio.create_task(broadcast(c.message.chat.id, d["src"], mk))

# ---------- anime qo'shish / tahrirlash ----------
@dp.message(New.name, F.text)
async def n_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(New.photo)
    await m.answer("🖼 Poster <b>rasmini</b> yuboring.\n<i>(rasm yoki fayl; o'tkazish: /skip)</i>")

async def after_photo(m, state):
    d = await state.get_data()
    if d.get("code"):
        await state.set_state(New.year)
        await m.answer("📅 <b>Yilini</b> yozing (masalan 2026):")
    else:
        await state.set_state(New.code)
        await m.answer("🔑 Anime <b>kodini</b> yozing (lotin harf, raqam, probelsiz).\n"
                       "Masalan: <code>kokzindon</code>")

@dp.message(New.photo, F.photo)
async def n_photo(m: Message, state: FSMContext):
    await state.update_data(photo=m.photo[-1].file_id)
    await after_photo(m, state)

@dp.message(New.photo, F.document.mime_type.startswith("image"))
async def n_photo_doc(m: Message, state: FSMContext):
    buf = await bot.download(m.document)
    sent = await bot.send_photo(m.chat.id, BufferedInputFile(buf.read(), "poster.jpg"),
                                caption="✅ Poster qabul qilindi")
    await state.update_data(photo=sent.photo[-1].file_id)
    await after_photo(m, state)

@dp.message(New.photo, Command("skip"))
async def n_photo_skip(m: Message, state: FSMContext):
    await state.update_data(photo=None)
    await after_photo(m, state)

@dp.message(New.photo)
async def n_photo_bad(m: Message):
    await m.answer("🖼 Iltimos, <b>rasm</b> yuboring (yoki /skip).")

@dp.message(New.code, F.text)
async def n_code(m: Message, state: FSMContext):
    code = clean_code(m.text)
    if not code:
        return await m.answer("❌ Kod faqat lotin harf va raqamdan iborat bo'lsin.")
    old = await db.titles.find_one({"_id": code})
    await state.update_data(code=code)
    await state.set_state(New.year)
    note = "\n⚠️ Bu kod bor: ma'lumotlari yangilanadi, qismlar saqlanadi." if old else ""
    await m.answer(f"✅ Kod: <code>{code}</code>{note}\n\n📅 <b>Yilini</b> yozing (masalan 2026):")

@dp.message(New.year, F.text)
async def n_year(m: Message, state: FSMContext):
    y = m.text.strip()
    if not re.fullmatch(r"\d{4}", y):
        return await m.answer("❌ Yilni 4 xonali raqam bilan yozing (masalan 2026).")
    await state.update_data(year=y)
    await state.set_state(New.lang)
    await m.answer("🌐 <b>Tilini</b> tanlang (yoki o'zingiz yozing):", reply_markup=kb(
        [(v, f"lg:{k}") for k, v in LANGS.items()], row=2))

async def ask_genre(msg, state, lang):
    await state.update_data(lang=lang)
    await state.set_state(New.genre)
    await msg.answer("🎭 <b>Janrlarni</b> probel bilan yozing\n(masalan: <code>komediya dahshat sarguzasht</code>)")

@dp.callback_query(New.lang, F.data.startswith("lg:"))
async def n_lang_btn(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await ask_genre(c.message, state, LANGS[c.data[3:]])

@dp.message(New.lang, F.text)
async def n_lang(m: Message, state: FSMContext):
    await ask_genre(m, state, m.text.strip())

@dp.message(New.genre, F.text)
async def n_genre(m: Message, state: FSMContext):
    d = await state.get_data()
    genre = " ".join("#" + w.lstrip("#") for w in m.text.split())
    upd = {"name": d["name"], "year": d["year"], "lang": d["lang"], "genre": genre}
    if d.get("photo"):
        upd["photo"] = d["photo"]
    await db.titles.update_one({"_id": d["code"]},
        {"$set": upd, "$setOnInsert": {"ts": time.time(), "views": 0}}, upsert=True)
    await m.answer(f"✅ Saqlandi: <b>{esc(d['name'])}</b> (<code>{d['code']}</code>)")
    if d.get("edit"):
        await state.clear()
        return await m.answer("Qism qo'shish kerakmi?",
                              reply_markup=kb([("➕ Qism qo'shish", f"ae:{d['code']}")], row=1))
    await ask_season(m, state, d["code"])

# ---------- qismlar yuklash ----------
async def ask_season(msg, state, code):
    await state.clear()
    await state.set_state(Up.season)
    await state.update_data(code=code)
    ex = sorted(await db.episodes.distinct("s", {"code": code}))
    await msg.answer("📂 <b>Mavsum raqamini</b> yozing (masalan 1).\n"
                     f"Mavjud mavsumlar: {', '.join(map(str, ex)) or 'yoq'}\n"
                     "<i>Bekor: /cancel</i>")

@dp.callback_query(F.data.startswith("ae:"), AdminF())
async def ae(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await ask_season(c.message, state, c.data[3:])

@dp.message(Up.season, F.text)
async def up_season(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit() or int(t) < 1:
        return await m.answer("❌ Mavsum raqamini yozing (1, 2, 3...).")
    d = await state.get_data()
    s = int(t)
    last = await db.episodes.find({"code": d["code"], "s": s}).sort("e", -1).limit(1).to_list(1)
    nxt = last[0]["e"] + 1 if last else 1
    await state.update_data(s=s, nxt=nxt)
    await state.set_state(Up.files)
    await m.answer(f"🎞 <b>{s}-mavsum</b>. Endi videolarni <b>ketma-ket</b> yuboring.\n"
                   f"Birinchisi <b>{nxt}-qism</b> bo'ladi, keyingilari avtomatik raqamlanadi.\n\n"
                   "• /set 5 — raqamni o'zgartirish\n• /done — tugatish",
                   reply_markup=kb([("✅ Tugatish", "up:done")], row=1))

async def finish_up(msg, state):
    d = await state.get_data()
    await state.clear()
    await msg.answer("✅ Tayyor! Animeni tekshirish uchun kodini yozing:\n"
                     f"<code>{d.get('code', '')}</code>")

@dp.callback_query(Up.files, F.data == "up:done")
async def up_done_btn(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await finish_up(c.message, state)

@dp.message(Up.files, Command("done"))
async def up_done(m: Message, state: FSMContext):
    await finish_up(m, state)

@dp.message(Up.files, Command("set"))
async def up_set(m: Message, command: CommandObject, state: FSMContext):
    if not command.args or not command.args.strip().isdigit():
        return await m.answer("Yozing: /set 5")
    await state.update_data(nxt=int(command.args.strip()))
    await m.answer(f"✅ Keyingi video {command.args.strip()}-qism bo'ladi.")

@dp.message(Up.files, F.video | F.document.mime_type.startswith("video"))
async def up_file(m: Message, state: FSMContext):
    lock = LOCKS.setdefault(m.from_user.id, asyncio.Lock())
    async with lock:
        d = await state.get_data()
        code, s, e = d["code"], d["s"], d["nxt"]
        if m.video:
            fid, kind = m.video.file_id, "video"
        else:
            fid, kind = m.document.file_id, "document"
        await db.episodes.update_one({"code": code, "s": s, "e": e},
            {"$set": {"fid": fid, "kind": kind}}, upsert=True)
        await state.update_data(nxt=e + 1)
    await m.reply(f"✅ {s}-mavsum • {e}-qism saqlandi")

@dp.message(Up.files)
async def up_bad(m: Message):
    await m.answer("🎞 Video yuboring. Tugatish: /done")

# ---------- animelarni boshqarish ----------
async def manage_page(page):
    total = await db.titles.count_documents({})
    if not total:
        return "Hozircha anime yo'q.", kb([("🎬 Anime qo'shish", "adm:new")], row=1)
    pages = (total + PER - 1) // PER
    page = max(0, min(page, pages - 1))
    items = await db.titles.find().sort("ts", -1).skip(page * PER).limit(PER).to_list(PER)
    rows = [[btn("🎬 " + x.get("name", x["_id"]), f"mg:{x['_id']}")] for x in items]
    rows.append(nav_row("mgp", page, pages))
    return f"🎞 <b>Animelar</b> (jami {total})", InlineKeyboardMarkup(inline_keyboard=rows)

@dp.callback_query(F.data.startswith("mgp:"), AdminF())
async def mgp(c: CallbackQuery):
    t, mk = await manage_page(int(c.data[4:]))
    try:
        await c.message.edit_text(t, reply_markup=mk)
    except Exception:
        pass
    await c.answer()

@dp.callback_query(F.data.startswith("mg:"), AdminF())
async def mg(c: CallbackQuery):
    code = c.data[3:]
    a = await db.titles.find_one({"_id": code})
    if not a:
        return await c.answer("Topilmadi", show_alert=True)
    n = await db.episodes.count_documents({"code": code})
    await c.message.answer(f"🎬 <b>{esc(a.get('name', code))}</b>\n🔑 <code>{code}</code> • 📺 {n} qism",
        reply_markup=kb([("➕ Qism qo'shish", f"ae:{code}"), ("✏️ Tahrirlash", f"ed:{code}"),
                         ("🗑 O'chirish", f"dl:{code}")], row=2))
    await c.answer()

@dp.callback_query(F.data.startswith("ed:"), AdminF())
async def ed(c: CallbackQuery, state: FSMContext):
    code = c.data[3:]
    await state.clear()
    await state.set_state(New.name)
    await state.update_data(code=code, edit=True)
    await c.message.answer("✏️ Yangi <b>nomini</b> yozing:")
    await c.answer()

@dp.callback_query(F.data.startswith("dl:"), AdminF())
async def dl(c: CallbackQuery):
    code = c.data[3:]
    await c.message.answer(f"⚠️ <code>{code}</code> va uning barcha qismlari o'chadi. Aniqmi?",
        reply_markup=kb([("🗑 Ha, o'chirish", f"dy:{code}"), ("✖️ Yo'q", "noop")], row=2))
    await c.answer()

@dp.callback_query(F.data.startswith("dy:"), AdminF())
async def dy(c: CallbackQuery):
    code = c.data[3:]
    await db.titles.delete_one({"_id": code})
    r = await db.episodes.delete_many({"code": code})
    await c.message.answer(f"🗑 O'chirildi: {code} ({r.deleted_count} qism)")
    await c.answer()

@dp.message(Command("delep"), AdminF())
async def delep(m: Message, command: CommandObject):
    p = (command.args or "").split()
    if len(p) != 3 or not p[1].isdigit() or not p[2].isdigit():
        return await m.answer("Yozing: /delep kod mavsum qism\nMasalan: /delep kokzindon 1 3")
    r = await db.episodes.delete_one({"code": p[0].lower(), "s": int(p[1]), "e": int(p[2])})
    await m.answer("🗑 O'chirildi." if r.deleted_count else "Topilmadi.")

# ---------- eski usul: video + izoh "kod mavsum qism" ----------
@dp.message(F.video | F.document.mime_type.startswith("video"), AdminF(), StateFilter(None))
async def legacy_add(m: Message):
    p = (m.caption or "").split()
    if len(p) != 3 or not p[1].isdigit() or not p[2].isdigit():
        return await m.answer("ℹ️ Qism qo'shish uchun: /admin → 🎞 Animelarni boshqarish → ➕ Qism qo'shish.\n"
                              "Yoki video izohiga yozing: <code>kod mavsum qism</code>")
    code = clean_code(p[0])
    if m.video:
        fid, kind = m.video.file_id, "video"
    else:
        fid, kind = m.document.file_id, "document"
    await db.titles.update_one({"_id": code},
        {"$setOnInsert": {"name": code, "ts": time.time(), "views": 0}}, upsert=True)
    await db.episodes.update_one({"code": code, "s": int(p[1]), "e": int(p[2])},
        {"$set": {"fid": fid, "kind": kind}}, upsert=True)
    await m.answer(f"✅ Saqlandi: {code} • {p[1]}-mavsum • {p[2]}-qism")

# ---------- qidiruv (eng oxirida) ----------
@dp.message(F.text, ~F.text.startswith("/"), StateFilter(None))
async def search(m: Message):
    q = m.text.strip().lower()
    code = clean_code(q)
    if code and await db.titles.find_one({"_id": code}):
        return await show_card(m, code, m.from_user.id)
    found = await db.titles.find(
        {"name": {"$regex": re.escape(q), "$options": "i"}}).limit(10).to_list(10)
    if len(found) == 1:
        return await show_card(m, found[0]["_id"], m.from_user.id)
    if found:
        return await m.answer("🔎 <b>Topilgan animelar:</b>", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[btn("🎬 " + x.get("name", x["_id"]), f"a:{x['_id']}")] for x in found]))
    await m.answer("😔 <b>Topilmadi.</b>\nNomini yoki kodini tekshiring, yoki 📃 Animelar bo'limini oching.")

# ================= ishga tushirish =================
async def main():
    global BOT_USER
    BOT_USER = (await bot.get_me()).username
    await load_ad()
    await db.episodes.create_index([("code", 1), ("s", 1), ("e", 1)])
    await db.titles.create_index("name")
    await db.users.create_index("last")
    await bot.delete_webhook(drop_pending_updates=True)
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await dp.start_polling(bot)

asyncio.run(main())

import os, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton)
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

bot = Bot(os.environ["BOT_TOKEN"])
ADMIN = int(os.environ["ADMIN_ID"])
db = AsyncIOMotorClient(os.environ["MONGO_URL"]).animebot
dp = Dispatcher()

def num(x):
    return (not x.isdigit(), int(x) if x.isdigit() else 0, x)

def kb(buttons, row=3):
    b = [InlineKeyboardButton(text=t, callback_data=d) for t, d in buttons]
    return InlineKeyboardMarkup(
        inline_keyboard=[b[i:i+row] for i in range(0, len(b), row)])

@dp.message(CommandStart())
async def start(m: Message):
    await db.users.update_one({"_id": m.from_user.id}, {"$set": {"n": 1}}, upsert=True)
    await m.answer(
        "Xush kelibsiz! 👋\nQanday animelarni topib beray?\n\n"
        "Anime kodini yozing yoki ro'yxatdan tanlang.",
        reply_markup=kb([("📃 Anime ro'yxati", "list")], row=1))

@dp.message(Command("nom"), F.from_user.id == ADMIN)
async def nom(m: Message):
    p = m.text.split(maxsplit=2)
    if len(p) < 3:
        return await m.answer("Yozing: /nom kod Anime nomi")
    await db.animes.update_one({"_id": p[1].lower()}, {"$set": {"name": p[2]}}, upsert=True)
    await m.answer("Nomi saqlandi ✅")

@dp.message(F.video, F.from_user.id == ADMIN)
async def add(m: Message):
    p = (m.caption or "").split()
    if len(p) != 3:
        return await m.answer("Izoh: kod mavsum qism\nMasalan: kokzindon 1 1")
    code, s, e = p[0].lower(), p[1], p[2]
    await db.animes.update_one({"_id": code}, {"$setOnInsert": {"name": code}}, upsert=True)
    await db.videos.update_one({"code": code, "s": s, "e": e},
                               {"$set": {"fid": m.video.file_id}}, upsert=True)
    await m.answer(f"Saqlandi ✅ {code} mavsum {s} qism {e}")

async def show_seasons(msg, code):
    a = await db.animes.find_one({"_id": code})
    if not a:
        return await msg.answer("Topilmadi 😔 Kodni tekshiring yoki ro'yxatni oching: /start")
    seasons = await db.videos.distinct("s", {"code": code})
    if not seasons:
        return await msg.answer("Hozircha qismlar yo'q.")
    seasons.sort(key=num)
    await msg.answer(f"🎬 {a['name']}\nMavsumni tanlang:",
                     reply_markup=kb([(f"{s}-mavsum", f"s:{code}:{s}") for s in seasons]))

@dp.callback_query(F.data == "list")
async def lst(c: CallbackQuery):
    items = [x async for x in db.animes.find().limit(50)]
    if not items:
        await c.message.answer("Ro'yxat hozircha bo'sh.")
    else:
        await c.message.answer("📃 Anime ro'yxati:",
            reply_markup=kb([(x["name"], f"a:{x['_id']}") for x in items], row=1))
    await c.answer()

@dp.callback_query(F.data.startswith("a:"))
async def pick(c: CallbackQuery):
    await show_seasons(c.message, c.data[2:])
    await c.answer()

@dp.callback_query(F.data.startswith("s:"))
async def season(c: CallbackQuery):
    _, code, s = c.data.split(":")
    eps = await db.videos.distinct("e", {"code": code, "s": s})
    eps.sort(key=num)
    await c.message.answer(f"{s}-mavsum. Qismni tanlang:",
        reply_markup=kb([(e, f"e:{code}:{s}:{e}") for e in eps], row=4))
    await c.answer()

@dp.callback_query(F.data.startswith("e:"))
async def episode(c: CallbackQuery):
    _, code, s, e = c.data.split(":")
    v = await db.videos.find_one({"code": code, "s": s, "e": e})
    if v:
        await c.message.answer_video(v["fid"], caption=f"{code} | {s}-mavsum | {e}-qism")
    await c.answer()

@dp.message(F.text)
async def text(m: Message):
    await show_seasons(m, m.text.strip().lower())

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await dp.start_polling(bot)

asyncio.run(main())

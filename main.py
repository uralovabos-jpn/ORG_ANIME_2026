import os, json, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import (Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton)
from aiohttp import web

bot = Bot(os.environ["BOT_TOKEN"])
ADMIN = int(os.environ["ADMIN_ID"])
dp = Dispatcher()

def load():
    try: return json.load(open("db.json"))
    except: return {}

def order(x):
    return (not x.isdigit(), int(x) if x.isdigit() else 0, x)

def kb(items, prefix, row=4):
    b = [InlineKeyboardButton(text=i, callback_data=prefix + i)
         for i in sorted(items, key=order)]
    return InlineKeyboardMarkup(
        inline_keyboard=[b[i:i+row] for i in range(0, len(b), row)])

@dp.message(F.video, F.from_user.id == ADMIN)
async def add(m: Message):
    p = (m.caption or "").split()
    if len(p) != 3:
        return await m.answer("Подпись: код сезон серия\nНапример: naruto 1 5")
    code, s, e = p
    d = load()
    d.setdefault(code.lower(), {}).setdefault(s, {})[e] = m.video.file_id
    json.dump(d, open("db.json", "w"))
    await m.answer(f"Сохранено ✅ {code} сезон {s} серия {e}")

@dp.message(F.text)
async def get(m: Message):
    code = m.text.strip().lower()
    seasons = load().get(code)
    if not seasons:
        return await m.answer("Напиши код аниме, например: naruto")
    await m.answer("Выбери сезон:", reply_markup=kb(seasons, f"s:{code}:"))

@dp.callback_query(F.data.startswith("s:"))
async def season(c: CallbackQuery):
    _, code, s = c.data.split(":")
    eps = load().get(code, {}).get(s, {})
    await c.message.answer(f"Сезон {s}. Выбери серию:",
                           reply_markup=kb(eps, f"e:{code}:{s}:"))
    await c.answer()

@dp.callback_query(F.data.startswith("e:"))
async def episode(c: CallbackQuery):
    _, code, s, e = c.data.split(":")
    fid = load().get(code, {}).get(s, {}).get(e)
    if fid:
        await c.message.answer_video(fid, caption=f"{code}, сезон {s}, серия {e}")
    await c.answer()

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await dp.start_polling(bot)

asyncio.run(main())

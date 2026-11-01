BASE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:%(w)dpx;height:%(h)dpx;overflow:hidden;font-family:'Inter Display','Inter',sans-serif;-webkit-font-smoothing:antialiased}
%(css)s</style></head><body>%(body)s</body></html>"""

PLANE = '<path d="M3 11.5 21 4l-3.2 16.2-5.6-4.3-3.1 3V14.6L17 7.4 7.4 13.3 3 11.5Z" fill="currentColor"/>'

designs = {}

# ---------- AVATARS 640x640 (Telegram crops to a circle) ----------
designs["avatar_1_blue"] = (640, 640, """
.bg{width:640px;height:640px;background:radial-gradient(120% 120% at 20% 10%,#5AC8FA 0%,#2AABEE 35%,#1272D6 75%,#0B4FA8 100%);display:flex;align-items:center;justify-content:center;position:relative}
.ring{position:absolute;width:520px;height:520px;border-radius:50%;border:2px solid rgba(255,255,255,.18)}
.card{width:300px;height:300px;border-radius:84px;background:rgba(255,255,255,.14);backdrop-filter:blur(8px);border:2px solid rgba(255,255,255,.35);display:flex;flex-direction:column;align-items:center;justify-content:center;box-shadow:0 30px 60px rgba(4,40,100,.35)}
.plus{font-weight:900;font-size:168px;letter-spacing:-8px;color:#fff;line-height:1;transform:translateX(-10px)}
.name{margin-top:6px;font-weight:800;font-size:34px;letter-spacing:10px;padding-left:10px;color:rgba(255,255,255,.92)}
""", """<div class="bg"><div class="ring"></div><div class="card"><div class="plus">+1</div><div class="name">SMENA</div></div></div>""")

designs["avatar_2_dark"] = (640, 640, """
.bg{width:640px;height:640px;background:#0B0F17;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.glow{position:absolute;width:520px;height:520px;border-radius:50%;background:radial-gradient(circle,#2AABEE55 0%,#2AABEE00 65%)}
svg{position:relative;width:360px;height:360px;filter:drop-shadow(0 0 28px #2AABEEaa)}
""", """<div class="bg"><div class="glow"></div>
<svg viewBox="0 0 100 100"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7DD6FF"/><stop offset="1" stop-color="#2A7FEE"/></linearGradient></defs>
<g><path d="M21.99 42.49 A29 29 0 0 1 76.28 37.74" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/><path d="M80.09 45.90 L83.11 33.46 L68.61 40.22Z" fill="url(#g)" stroke="url(#g)" stroke-width="2" stroke-linejoin="round"/></g><g transform="rotate(180 50 50)"><path d="M21.99 42.49 A29 29 0 0 1 76.28 37.74" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/><path d="M80.09 45.90 L83.11 33.46 L68.61 40.22Z" fill="url(#g)" stroke="url(#g)" stroke-width="2" stroke-linejoin="round"/></g>
<text x="48.5" y="58.6" text-anchor="middle" font-family="Inter Display" font-weight="900" font-size="24" fill="#fff" letter-spacing="-1">+1</text>
</svg></div>""")

designs["avatar_3_usa"] = (640, 640, """
.bg{width:640px;height:640px;background:linear-gradient(160deg,#16244F 0%,#0E1838 100%);position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center}
.stripes{position:absolute;inset:0;background:repeating-linear-gradient(0deg,transparent 0 44px,rgba(255,255,255,.035) 44px 88px)}
.stars{position:absolute;top:140px;left:0;right:0;text-align:center;color:#fff;font-size:36px;letter-spacing:24px;padding-left:24px;opacity:.9}
.plus{position:relative;font-weight:900;font-size:230px;letter-spacing:-12px;color:#fff;line-height:1;transform:translateX(-12px)}
.bar{position:absolute;bottom:170px;width:200px;height:16px;border-radius:8px;background:linear-gradient(90deg,#E63946,#FF6B6B)}
.plane{position:absolute;top:118px;right:150px;width:64px;height:64px;color:#2AABEE;transform:rotate(-8deg)}
""", f"""<div class="bg"><div class="stripes"></div><div class="stars">★ ★ ★</div><div class="plus">+1</div><div class="bar"></div></div>""")

# ---------- BANNERS 1280x720 (bot description picture is 640x360, same 16:9) ----------
CHIPS = '<div class="chips"><span>60+ дней аренды</span><span>Код прямо в чат</span><span>Оплата криптой</span></div>'

designs["banner_1_blue"] = (1280, 720, """
.bg{width:1280px;height:720px;background:radial-gradient(90% 120% at 0% 0%,#5AC8FA 0%,#2AABEE 30%,#1272D6 70%,#0B4FA8 100%);position:relative;overflow:hidden;color:#fff}
.left{position:absolute;left:88px;top:110px;width:640px}
.brand{font-weight:800;font-size:30px;letter-spacing:12px;opacity:.9}
h1{margin-top:22px;font-weight:900;font-size:86px;line-height:.98;letter-spacing:-3px}
p{margin-top:24px;font-size:30px;font-weight:500;opacity:.88}
.chips{margin-top:40px;display:flex;gap:12px}
.chips span{padding:12px 18px;border-radius:999px;background:rgba(255,255,255,.16);border:1.5px solid rgba(255,255,255,.35);font-weight:600;font-size:19px;white-space:nowrap}
.phone{position:absolute;right:110px;top:90px;width:340px;height:560px;border-radius:56px;background:#0E1626;border:10px solid #0A1020;box-shadow:0 40px 80px rgba(3,30,80,.45);transform:rotate(6deg);padding:70px 22px}
.bubble{background:#1E2C44;border-radius:22px 22px 22px 6px;padding:20px 22px;color:#fff;margin-bottom:16px}
.bubble .t{font-size:17px;opacity:.7;font-weight:600}
.bubble .code{margin-top:6px;font-family:'JetBrains Mono';font-weight:800;font-size:46px;letter-spacing:4px;color:#7DD6FF}
.bubble.small{font-size:18px;font-weight:600}
.hint{margin-top:18px;text-align:center;color:#6B7A93;font-size:16px;font-weight:500}
.btn{margin-top:10px;background:#2AABEE;border-radius:14px;padding:14px;text-align:center;font-weight:700;font-size:18px}
""", f"""<div class="bg"><div class="left"><div class="brand">SMENA</div><h1>Номера Telegram<br>США · +1</h1><p>Виртуальный номер за минуту — код входа придёт прямо в бот.</p>{CHIPS}</div>
<div class="phone"><div class="bubble small">✅ Номер готов<br><span style="font-family:JetBrains Mono;color:#7DD6FF">+1 775 295 3734</span></div>
<div class="bubble"><div class="t">💬 Код входа в Telegram</div><div class="code">48512</div></div><div class="btn">✍️ Скопировать код</div><div class="hint">Новые коды тоже придут сюда</div></div></div>""")

designs["banner_2_dark"] = (1280, 720, """
.bg{width:1280px;height:720px;background:#0B0F17;position:relative;overflow:hidden;color:#fff}
.glow{position:absolute;right:-120px;top:-160px;width:900px;height:900px;border-radius:50%;background:radial-gradient(circle,#2AABEE40 0%,#2AABEE00 60%)}
.big{position:absolute;right:30px;top:150px;font-weight:900;font-size:400px;line-height:1;letter-spacing:-26px;color:transparent;-webkit-text-stroke:3px #2AABEE55}
.left{position:absolute;left:90px;bottom:110px}
.brand{display:flex;align-items:center;gap:16px;font-weight:800;font-size:34px;letter-spacing:10px}
.brand i{width:16px;height:16px;border-radius:50%;background:#2AABEE;box-shadow:0 0 18px #2AABEE}
h1{margin-top:26px;font-weight:900;font-size:86px;line-height:.98;letter-spacing:-3px}
h1 b{color:#2AABEE}
p{margin-top:26px;font-size:28px;color:#9BA8BD;font-weight:500}
.chips{margin-top:34px;display:flex;gap:14px}
.chips span{padding:13px 22px;border-radius:12px;background:#151C29;border:1px solid #243049;font-weight:600;font-size:21px;color:#D7E2F2}
""", f"""<div class="bg"><div class="glow"></div><div class="big">+1</div><div class="left"><div class="brand"><i></i>SMENA</div>
<h1>Telegram-номера<br><b>США</b> без лишнего</h1><p>Купил → вошёл → код уже в чате.</p>{CHIPS}</div></div>""")

designs["banner_3_usa"] = (1280, 720, """
.bg{width:1280px;height:720px;background:linear-gradient(150deg,#1A2A5E 0%,#0E1838 100%);position:relative;overflow:hidden;color:#fff}
.stripes{position:absolute;right:0;top:0;width:520px;height:720px;background:repeating-linear-gradient(180deg,rgba(255,255,255,.07) 0 56px,transparent 56px 112px);-webkit-mask:linear-gradient(90deg,transparent,#000 40%)}
.stars{position:absolute;right:70px;top:70px;font-size:34px;letter-spacing:26px;line-height:2;width:420px;text-align:right;opacity:.85}
.plus{position:absolute;right:90px;bottom:70px;background:linear-gradient(180deg,#fff 60%,#FF6B6B);-webkit-background-clip:text;color:transparent;font-weight:900;font-size:300px;letter-spacing:-14px;line-height:1}
.left{position:absolute;left:90px;top:130px;width:640px}
.tag{display:inline-block;padding:10px 18px;border-radius:10px;background:#E63946;font-weight:800;font-size:22px;letter-spacing:3px}
h1{margin-top:28px;font-weight:900;font-size:92px;line-height:.98;letter-spacing:-3px}
p{margin-top:24px;font-size:28px;opacity:.8;font-weight:500}
.chips{margin-top:38px;display:flex;gap:12px}
.chips span{padding:12px 18px;border-radius:999px;border:1.5px solid rgba(255,255,255,.4);font-weight:600;font-size:19px;white-space:nowrap}
""", f"""<div class="bg"><div class="stripes"></div><div class="stars">★ ★ ★ ★<br>★ ★ ★ ★</div><div class="plus">+1</div>
<div class="left"><span class="tag">SMENA</span><h1>Американские<br>номера Telegram</h1><p>Виртуальные номера +1 · коды приходят в бот</p>{CHIPS}</div></div>""")

if __name__ == "__main__":
    import asyncio
    from playwright.async_api import async_playwright

    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            for name, (w, h, css, body) in designs.items():
                page = await browser.new_page(viewport={"width": w, "height": h})
                await page.set_content(BASE % {"w": w, "h": h, "css": css, "body": body})
                await page.wait_for_timeout(300)
                await page.screenshot(path=f"{name}.png")
                print(name)
            await browser.close()
    asyncio.run(main())

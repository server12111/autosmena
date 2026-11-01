import json
HEAD = """<!doctype html><html><head><meta charset="utf-8"><style>*{margin:0;padding:0;box-sizing:border-box}
body{width:1280px;height:720px;overflow:hidden;font-family:'Inter Display','Inter',sans-serif;-webkit-font-smoothing:antialiased}
.bg{width:1280px;height:720px;position:relative;overflow:hidden;color:#fff;background:radial-gradient(90% 120% at 10% 0%,#24408A 0%,#1A2A5E 45%,#0E1838 100%)}
.glass{background:linear-gradient(135deg,rgba(255,255,255,.16),rgba(255,255,255,.05));border:1.5px solid rgba(255,255,255,.26);box-shadow:inset 0 1px 0 rgba(255,255,255,.45),0 30px 60px rgba(4,10,35,.45);backdrop-filter:blur(16px)}
.left{position:absolute;left:84px;top:50%;transform:translateY(-50%);width:640px}
.hook{display:inline-block;padding:10px 18px;border-radius:10px;background:#E63946;font-weight:800;font-size:22px;letter-spacing:2px}
h1{margin-top:22px;font-weight:900;font-size:72px;line-height:1.04;letter-spacing:-2px}
h1 em{font-style:normal;color:#7DD6FF}
.list{margin-top:26px;display:flex;flex-direction:column;gap:12px}
.list div{font-size:27px;font-weight:600;color:#DCE6F7;display:flex;align-items:center;gap:14px}
.list b{display:inline-flex;width:34px;height:34px;border-radius:50%;background:#2FB566;align-items:center;justify-content:center;font-size:20px;flex:none}
.cta{margin-top:30px;display:inline-flex;align-items:center;gap:14px;padding:18px 30px;border-radius:18px;background:linear-gradient(180deg,#34C46F,#23A158);font-weight:900;font-size:28px;box-shadow:0 18px 40px rgba(47,181,102,.45),inset 0 2px 0 rgba(255,255,255,.3)}
.glowr{position:absolute;right:-60px;top:50%;transform:translateY(-50%);width:760px;height:760px;border-radius:50%;background:radial-gradient(circle,rgba(42,171,238,.3),transparent 62%)}
@@CSS@@</style></head><body><div class="bg"><div class="glowr"></div>@@BODY@@</div></body></html>"""
out = {}
def page(name, css, body):
    open(name + '.html', 'w').write(HEAD.replace('@@CSS@@', css).replace('@@BODY@@', body)); out[name] = [1280, 720]

LIST = '''<div class="list"><div><b>✓</b>Номер выдаётся за 10 секунд</div><div><b>✓</b>Код входа приходит прямо в бот</div><div><b>✓</b>Аренда больше 60 дней</div></div>'''

# A — price hero: giant glass price tag
page("sell_price", """
.tag{position:absolute;right:96px;top:50%;transform:translateY(-50%) rotate(-4deg);width:420px;padding:40px 40px 36px;border-radius:40px;text-align:center}
.tag .l{font-size:24px;font-weight:700;color:#A9B8D6;letter-spacing:1px}
.tag .p{margin-top:6px;font-weight:900;font-size:124px;letter-spacing:-4px;line-height:1;background:linear-gradient(180deg,#fff 40%,#BFE6FF);-webkit-background-clip:text;color:transparent}
.tag .d{margin-top:14px;display:inline-block;padding:10px 18px;border-radius:12px;background:rgba(47,181,102,.18);border:1px solid rgba(47,181,102,.55);color:#7CF0A0;font-weight:800;font-size:22px}
.tag .n{margin-top:22px;font-family:'JetBrains Mono';font-weight:700;font-size:26px;color:#DCE6F7}
.flag{position:absolute;top:-22px;right:-18px;padding:12px 18px;border-radius:14px;background:#E63946;font-weight:900;font-size:22px;transform:rotate(8deg);box-shadow:0 12px 26px rgba(230,57,70,.45)}
""", f"""<div class="left"><span class="hook">🇺🇸 НОМЕР США</span><h1><em>Американский</em><br>номер Telegram</h1>{LIST}<div class="cta">Купить сейчас →</div></div>
<div class="glass tag"><div class="flag">+1</div><div class="l">ЦЕНА НОМЕРА</div><div class="p">$0.39</div><div class="d">меньше 1 цента в день</div><div class="n">+1 775 ••• 3734</div></div>""")

# B — problem → solution hook with the code card
page("sell_hook", """
.stack{position:absolute;right:80px;top:50%;transform:translateY(-50%);width:440px}
.c{padding:24px 26px;border-radius:26px}
.c .l{font-size:18px;font-weight:600;color:#A9B8D6}
.c .v{margin-top:8px;font-family:'JetBrains Mono';font-weight:800;font-size:30px}
.code{margin-top:4px;font-family:'JetBrains Mono';font-weight:800;font-size:72px;letter-spacing:8px;color:#9BE0FF}
.c2{margin-top:16px;margin-left:40px;background:linear-gradient(135deg,rgba(42,171,238,.36),rgba(18,114,214,.2))}
.price{position:absolute;z-index:2;right:-6px;top:-40px;padding:14px 22px;border-radius:18px;background:#E63946;font-weight:900;font-size:34px;transform:rotate(6deg);box-shadow:0 14px 30px rgba(230,57,70,.45)}
.time{margin-top:16px;margin-left:10px;display:inline-flex;gap:10px;align-items:center;padding:14px 20px;border-radius:16px;font-weight:800;font-size:22px}
""", f"""<div class="left"><span class="hook">НУЖЕН ВТОРОЙ АККАУНТ?</span><h1>Номер США<br>для Telegram<br><em>за минуту</em></h1>{LIST}<div class="cta">Получить номер →</div></div>
<div class="stack"><div class="price">$0.39</div>
<div class="glass c"><div class="l">📱 Ваш номер</div><div class="v">+1 775 295 3734</div></div>
<div class="glass c c2"><div class="l">💬 Код входа в Telegram</div><div class="code">48512</div></div>
<div class="glass time">⚡ Код уже в чате</div></div>""")
json.dump(out, open('list.json', 'w'))

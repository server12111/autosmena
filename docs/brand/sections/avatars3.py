import json
HEAD = """<!doctype html><html><head><meta charset="utf-8"><style>*{margin:0;padding:0;box-sizing:border-box}
body{width:640px;height:640px;overflow:hidden;font-family:'Inter Display','Inter',sans-serif;-webkit-font-smoothing:antialiased}
.bg{width:640px;height:640px;position:relative;overflow:hidden;color:#fff;display:flex;align-items:center;justify-content:center}
@@CSS@@</style></head><body><div class="bg">@@BODY@@</div></body></html>"""
out = {}
def av(name, css, body):
    open(name + '.html', 'w').write(HEAD.replace('@@CSS@@', css).replace('@@BODY@@', body)); out[name] = [640, 640]

# 1 — a glass phone on brand navy with the +1 on screen and a red notification dot
av("ava_phone", """
.bg{background:radial-gradient(90% 90% at 30% 15%,#2B4BA0 0%,#1A2A5E 50%,#0E1838 100%)}
.halo{position:absolute;width:460px;height:460px;border-radius:50%;background:radial-gradient(circle,rgba(90,200,250,.35),transparent 65%)}
.phone{position:relative;width:250px;height:410px;border-radius:58px;background:linear-gradient(160deg,rgba(255,255,255,.28),rgba(255,255,255,.07) 60%,rgba(255,255,255,.14));border:3px solid rgba(255,255,255,.55);box-shadow:inset 0 2px 0 rgba(255,255,255,.75),0 40px 80px rgba(4,10,35,.55);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(10px)}
.island{position:absolute;top:22px;width:84px;height:24px;border-radius:12px;background:rgba(10,18,48,.6)}
.plus{font-weight:900;font-size:150px;letter-spacing:-8px;margin-left:-8px;background:linear-gradient(180deg,#fff 40%,#CFE0FF);-webkit-background-clip:text;color:transparent}
.dot{position:absolute;top:-18px;right:-18px;width:76px;height:76px;border-radius:50%;background:#E63946;border:6px solid #1A2A5E;box-shadow:0 10px 24px rgba(230,57,70,.5)}
""", '<div class="halo"></div><div class="phone"><div class="island"></div><div class="plus">+1</div><div class="dot"></div></div>')

# 2 — SMENA wordmark stacked: big S monogram made of two arcs (swap), Telegram blue on white
av("ava_swap", """
.bg{background:linear-gradient(145deg,#5AC8FA 0%,#2AABEE 45%,#1272D6 100%)}
.ring{position:absolute;width:520px;height:520px;border-radius:50%;border:2px solid rgba(255,255,255,.2)}
svg{width:400px;height:400px;filter:drop-shadow(0 24px 40px rgba(6,40,110,.45))}
""", '''<div class="ring"></div><svg viewBox="0 0 100 100">
<path d="M70 27 A27 27 0 0 0 23 44" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round"/>
<path d="M30 73 A27 27 0 0 0 77 56" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round"/>
<path d="M62 17 L76 26 L64 37" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M38 83 L24 74 L36 63" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="50" cy="50" r="9" fill="#E63946"/></svg>''')

# 3 — bold wordmark: white SMENA on deep navy with a red underline and a tiny +1 badge
av("ava_wordmark", """
.bg{background:radial-gradient(100% 100% at 50% 0%,#24408A 0%,#16244F 55%,#0B1430 100%);flex-direction:column}
.word{font-weight:900;font-size:132px;letter-spacing:-3px;line-height:1;margin-left:-4px}
.line{margin-top:18px;width:300px;height:14px;border-radius:7px;background:linear-gradient(90deg,#E63946,#FF7A6B)}
.badge{margin-top:34px;padding:12px 26px;border-radius:999px;background:rgba(255,255,255,.12);border:2px solid rgba(255,255,255,.35);font-weight:800;font-size:38px;letter-spacing:2px}
""", '<div class="word">SMENA</div><div class="line"></div><div class="badge">+1 USA</div>')
json.dump(out, open('list.json', 'w'))

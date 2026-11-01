import json
HEAD = """<!doctype html><html><head><meta charset="utf-8"><style>*{margin:0;padding:0;box-sizing:border-box}
body{width:640px;height:640px;overflow:hidden;font-family:'Inter Display','Inter',sans-serif;-webkit-font-smoothing:antialiased}
.bg{width:640px;height:640px;position:relative;overflow:hidden;color:#fff;display:flex;align-items:center;justify-content:center;
 background:radial-gradient(90% 90% at 25% 10%,#24408A 0%,#1A2A5E 45%,#0E1838 100%)}
.glow{position:absolute;border-radius:50%}
.glass{background:linear-gradient(135deg,rgba(255,255,255,.2),rgba(255,255,255,.05) 60%,rgba(255,255,255,.1));border:2px solid rgba(255,255,255,.32);
 box-shadow:inset 0 2px 0 rgba(255,255,255,.5),0 40px 80px rgba(4,10,35,.55);backdrop-filter:blur(14px)}
@@CSS@@</style></head><body><div class="bg">@@BODY@@</div></body></html>"""
out = {}
def logo(name, css, body):
    open(name + '.html', 'w').write(HEAD.replace('@@CSS@@', css).replace('@@BODY@@', body)); out[name] = [640, 640]

# 1 — the red brand tag from the banners, as the mark
logo("logo_tag", """
.glow{width:560px;height:560px;background:radial-gradient(circle,rgba(230,57,70,.28),transparent 62%)}
.tag{position:relative;padding:36px 44px 36px 50px;border-radius:38px;background:linear-gradient(180deg,#F04452,#D42E3C);font-weight:900;font-size:84px;letter-spacing:6px;
 box-shadow:inset 0 3px 0 rgba(255,255,255,.35),0 34px 70px rgba(150,20,35,.5)}
""", '<div class="glow"></div><div class="tag">SMENA</div>')

# 2 — glass tile with a bold S and a red status dot
logo("logo_tile", """
.glow{width:560px;height:560px;background:radial-gradient(circle,rgba(90,200,250,.32),transparent 62%)}
.tile{position:relative;width:330px;height:330px;border-radius:92px;display:flex;align-items:center;justify-content:center}
.tile b{font-weight:900;font-size:250px;line-height:1;margin-top:-14px;background:linear-gradient(180deg,#fff 45%,#CFE0FF);-webkit-background-clip:text;color:transparent}
.dot{position:absolute;top:-16px;right:-16px;width:84px;height:84px;border-radius:50%;background:#E63946;border:8px solid #1A2A5E;box-shadow:0 12px 28px rgba(230,57,70,.55)}
""", '<div class="glow"></div><div class="tile glass"><b>S</b><div class="dot"></div></div>')

# 3 — swap monogram: two arrows forming an S inside a glass ring, red core
logo("logo_swap", """
.glow{width:560px;height:560px;background:radial-gradient(circle,rgba(42,171,238,.3),transparent 62%)}
.ring{position:relative;width:400px;height:400px;border-radius:50%;display:flex;align-items:center;justify-content:center}
svg{width:300px;height:300px;filter:drop-shadow(0 14px 30px rgba(4,10,35,.5))}
""", '''<div class="glow"></div><div class="ring glass"><svg viewBox="0 0 100 100">
<path d="M70 27 A27 27 0 0 0 23 44" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round"/>
<path d="M30 73 A27 27 0 0 0 77 56" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round"/>
<path d="M62 17 L76 26 L64 37" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M38 83 L24 74 L36 63" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="50" cy="50" r="9" fill="#E63946"/></svg></div>''')

# 4 — mini of the main banner: two tilted glass cards with the code
logo("logo_cards", """
.glow{width:560px;height:560px;background:radial-gradient(circle,rgba(42,171,238,.28),transparent 62%)}
.c{position:absolute;width:330px;padding:30px 32px;border-radius:34px}
.c1{transform:translate(-34px,-96px) rotate(-8deg)}
.c2{transform:translate(34px,74px) rotate(5deg);background:linear-gradient(135deg,rgba(42,171,238,.4),rgba(18,114,214,.22))}
.l{font-size:22px;font-weight:600;color:#A9B8D6}
.v{margin-top:8px;font-family:'JetBrains Mono';font-weight:800;font-size:36px;letter-spacing:1px;white-space:nowrap}
.code{margin-top:6px;font-family:'JetBrains Mono';font-weight:800;font-size:76px;letter-spacing:8px;color:#9BE0FF}
.dot{position:absolute;width:64px;height:64px;border-radius:50%;background:#E63946;border:7px solid #1A2A5E;transform:translate(140px,-176px);box-shadow:0 10px 24px rgba(230,57,70,.5)}
""", '<div class="glow"></div><div class="glass c c1"><div class="l">SMENA</div><div class="v">+1 ••• 3734</div></div><div class="glass c c2"><div class="l">код</div><div class="code">48512</div></div><div class="dot"></div>')

# 5 — wordmark S with the red underline, exactly like the main banner, cropped to a circle
logo("logo_word", """
.glow{width:560px;height:560px;background:radial-gradient(circle,rgba(90,200,250,.22),transparent 62%)}
.w{position:relative;display:flex;flex-direction:column;align-items:center}
.w b{font-weight:900;font-size:330px;line-height:.9;letter-spacing:-8px}
.u{margin-top:26px;width:190px;height:22px;border-radius:11px;background:linear-gradient(90deg,#E63946,#FF7A6B);box-shadow:0 10px 24px rgba(230,57,70,.45)}
""", '<div class="glow"></div><div class="w"><b>S</b><div class="u"></div></div>')
json.dump(out, open('list.json', 'w'))

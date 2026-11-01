import json, re
base = open('banner_3_noplus_dark.html').read()
# Strip the flag: keep the brand background, typography and chips.
_a = base.index('<div class="flag">'); _b = base.index('<div class="left">')
base = base[:_a] + base[_b:]
base = base.replace(".left{position:absolute;left:90px;top:130px;width:640px}", ".left{position:absolute;left:90px;top:50%;transform:translateY(-50%);width:600px}")

COMMON = """
.art{position:absolute;right:70px;top:50%;transform:translateY(-50%);width:500px;height:560px}
.glow{position:absolute;right:-80px;top:50%;transform:translateY(-50%);width:760px;height:760px;border-radius:50%;background:radial-gradient(circle,%(glow)s 0%,transparent 62%)}
.glass{position:absolute;border-radius:28px;background:linear-gradient(135deg,rgba(255,255,255,.16),rgba(255,255,255,.05));border:1.5px solid rgba(255,255,255,.22);box-shadow:inset 0 1px 0 rgba(255,255,255,.35),0 30px 60px rgba(4,10,35,.45);backdrop-filter:blur(16px)}
.muted{color:#93A3C2;font-weight:600}
.mono{font-family:'JetBrains Mono',monospace}
"""

def section(name, tag, h1, sub, chips, art_css, art_html, glow="rgba(42,171,238,.28)", h1size=92):
    s = base.replace("</style>", COMMON.replace("%(glow)s", glow) + art_css + "</style>")
    s = s.replace("h1{margin-top:28px;font-weight:900;font-size:92px;line-height:.98;letter-spacing:-3px}", f"h1{{margin-top:28px;font-weight:900;font-size:{h1size}px;line-height:1.06;letter-spacing:-1px}}")
    s = s.replace("p{margin-top:24px;", "p{margin-top:26px;line-height:1.35;")
    s = re.sub(r'<span class="tag">.*?</span>', f'<span class="tag">{tag}</span>', s)
    s = re.sub(r'<h1>.*?</h1>', f'<h1>{h1}</h1>', s)
    s = re.sub(r'<p>.*?</p>', f'<p>{sub}</p>', s)
    s = re.sub(r'<div class="chips">.*?</div>', '<div class="chips">' + ''.join(f'<span>{c}</span>' for c in chips) + '</div>', s)
    s = s.replace('<div class="left">', f'<div class="glow"></div><div class="art">{art_html}</div><div class="left">', 1)
    open(name + '.html', 'w').write(s)
    return name

names = []

# PROFILE — glass account card
PROFILE_ARGS = ("section_profile", "ПРОФИЛЬ", "Всё под<br>рукой", "Номера, рефералы и API — в одном месте", ["Мои номера", "Рефералы", "API"], """
.card{left:30px;top:70px;width:440px;height:420px;padding:36px}
.ava{width:84px;height:84px;border-radius:50%;background:linear-gradient(135deg,#5AC8FA,#1272D6);display:flex;align-items:center;justify-content:center;font-weight:900;font-size:36px;color:#fff;box-shadow:0 10px 30px rgba(42,171,238,.45)}
.row1{display:flex;align-items:center;gap:20px}
.nm{font-weight:800;font-size:30px;color:#fff}.id{font-size:19px;margin-top:4px}
.bal{margin-top:34px;font-size:18px}.bal b{display:block;margin-top:6px;font-size:58px;font-weight:900;color:#fff;letter-spacing:-2px}
.stats{margin-top:28px;display:flex;gap:14px}
.stats div{flex:1;padding:16px 18px;border-radius:18px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);font-size:16px}
.stats b{display:block;font-size:28px;color:#fff;font-weight:800;margin-top:4px}
.list{margin-top:34px;display:flex;flex-direction:column;gap:12px}
.list div{display:flex;align-items:center;padding:16px 18px;border-radius:18px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);font-size:21px;font-weight:700;color:#fff}
.list span{margin-left:auto;font-size:16px;font-weight:600;color:#93A3C2}
.badge{right:-10px;top:20px;padding:14px 20px;border-radius:18px;font-weight:800;font-size:20px;color:#fff}
""", """<div class="glass card"><div class="row1"><div class="ava">S</div><div><div class="nm">@smena_user</div><div class="id muted mono">ID 7145919720</div></div></div>
<div class="bal muted">Баланс<b>$24.60</b></div>
<div class="stats"><div class="muted">Номера<b>3</b></div><div class="muted">Рефералы<b>12</b></div></div></div>
""")
names.append(section(*PROFILE_ARGS))
names.append(section("base_profile", *PROFILE_ARGS[1:6], ""))

# WALLET — crypto coins + top-up card
WALLET_ARGS = ("section_wallet", "КОШЕЛЁК", "Пополнение<br>за минуту", "Баланс в долларах · оплата криптовалютой", ["Crypto Bot", "xRocket", "TON"], """
.coin{position:absolute;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;color:#fff;box-shadow:inset 0 -10px 24px rgba(0,0,0,.25),inset 0 6px 14px rgba(255,255,255,.35),0 24px 50px rgba(4,10,35,.5)}
.usdt{width:230px;height:230px;left:10px;top:30px;background:radial-gradient(circle at 35% 30%,#4FE3B5,#1BA27A 60%,#11785A);font-size:120px}
.ton{width:180px;height:180px;right:0;top:120px;background:radial-gradient(circle at 35% 30%,#7FD3FF,#0098EA 60%,#0068A8)}
.ton svg{width:92px;height:92px}
.pay{left:60px;top:300px;width:400px;padding:30px 32px}
.pay .t{font-size:18px}.pay b{display:block;font-size:56px;font-weight:900;color:#fff;margin-top:6px;letter-spacing:-2px}
.pay .m{display:flex;align-items:center;gap:12px;margin-top:14px;font-size:22px;font-weight:700;color:#fff}
.pay .m span{margin-left:auto;font-size:17px;font-weight:600;color:#93A3C2}
.pay .m i{width:12px;height:12px;border-radius:50%}.pay .g{background:#2FD38A}.pay .b{background:#5AC8FA}.pay .t2{background:#0098EA}
.ok{margin-top:18px;display:inline-block;padding:10px 16px;border-radius:12px;background:rgba(52,199,89,.18);border:1px solid rgba(52,199,89,.5);color:#7CF0A0;font-weight:700;font-size:18px}
""", """<div class="coin usdt"><svg viewBox="0 0 100 100" style="width:130px;height:130px"><rect x="22" y="22" width="56" height="13" rx="3" fill="#fff"/><rect x="43" y="22" width="14" height="58" rx="3" fill="#fff"/><ellipse cx="50" cy="47" rx="30" ry="8" fill="none" stroke="#fff" stroke-width="5"/></svg></div>
<div class="coin ton"><svg viewBox="0 0 56 56"><path d="M17 15h22c3 0 4.8 3.2 3.3 5.8L30.4 41.7a2.8 2.8 0 0 1-4.8 0L13.7 20.8C12.2 18.2 14 15 17 15Z" fill="none" stroke="#fff" stroke-width="4" stroke-linejoin="round"/><path d="M28 15v27" stroke="#fff" stroke-width="4"/></svg></div>
<div class="glass pay"><div class="t muted">Ваш баланс</div><b>$24.60</b><div class="ok">Зачисление сразу</div></div>""")
names.append(section(*WALLET_ARGS, glow="rgba(27,162,122,.22)"))
names.append(section("base_wallet", *WALLET_ARGS[1:6], WALLET_ARGS[6].split('<div class="glass pay">')[0], glow="rgba(27,162,122,.22)"))

# REFERRALS — network of friends with +5% bonuses
names.append(section("section_ref", "РЕФЕРАЛЫ", "Приглашай<br>и зарабатывай", "5% с пополнений друзей — сразу на баланс", ["Навсегда", "Без лимитов", "Моментально"], """
svg.lines{position:absolute;inset:0;width:500px;height:560px}
.node{position:absolute;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;color:#fff}
.me{width:150px;height:150px;left:175px;top:205px;background:linear-gradient(135deg,#FFB547,#FF7A3D);font-size:46px;box-shadow:0 0 0 12px rgba(255,170,70,.15),0 24px 50px rgba(4,10,35,.5)}
.fr{width:92px;height:92px;background:linear-gradient(135deg,#5AC8FA,#1272D6);font-size:32px;box-shadow:0 16px 36px rgba(4,10,35,.45)}
.bonus{position:absolute;padding:9px 14px;border-radius:12px;font-weight:800;font-size:20px;color:#FFD08A;background:rgba(255,170,70,.14);border:1px solid rgba(255,170,70,.45)}
""", """<svg class="lines" viewBox="0 0 500 560"><g stroke="rgba(255,255,255,.22)" stroke-width="2.5" stroke-dasharray="6 8">
<line x1="250" y1="280" x2="90" y2="96"/><line x1="250" y1="280" x2="410" y2="96"/><line x1="250" y1="280" x2="56" y2="400"/><line x1="250" y1="280" x2="444" y2="400"/><line x1="250" y1="280" x2="250" y2="500"/></g></svg>
<div class="node fr" style="left:44px;top:50px">A</div><div class="node fr" style="left:364px;top:50px">K</div>
<div class="node fr" style="left:10px;top:354px">M</div><div class="node fr" style="left:398px;top:354px">D</div><div class="node fr" style="left:204px;top:454px">R</div>
<div class="node me">5%</div>
<div class="bonus" style="left:120px;top:150px">+$0.50</div><div class="bonus" style="left:300px;top:160px">+$1.25</div><div class="bonus" style="left:330px;top:300px">+$0.25</div>""",
glow="rgba(255,150,60,.2)", h1size=76))

# API — code window
names.append(section("section_api", "API", "API для<br>реселлеров", "Покупка номеров и коды через HTTP", ["REST + JSON", "60 запросов/мин", "Цены магазина"], """
.win{left:0;top:70px;width:500px;height:420px;padding:0;overflow:hidden;background:rgba(9,14,32,.82)}
.bar{height:48px;display:flex;align-items:center;gap:9px;padding:0 20px;border-bottom:1px solid rgba(255,255,255,.08)}
.bar i{width:13px;height:13px;border-radius:50%}
.code{padding:24px 26px;font-family:'JetBrains Mono',monospace;font-size:19px;line-height:1.65;color:#C9D6EE;white-space:pre}
.k{color:#7DD6FF}.s{color:#9FE6B8}.m{color:#FFB547;font-weight:700}.c{color:#5F6F8C}.n{color:#F5A3FF}
""", """<div class="glass win"><div class="bar"><i style="background:#FF5F57"></i><i style="background:#FEBC2E"></i><i style="background:#28C840"></i></div>
<div class="code"><span class="m">POST</span> /reseller/v1/orders
<span class="c">Authorization: Bearer smn_••••</span>
{ <span class="k">"product_id"</span>: <span class="n">1</span> }

<span class="c">→ 200 OK</span>
{
  <span class="k">"status"</span>: <span class="s">"active"</span>,
  <span class="k">"phone"</span>: <span class="s">"+17752953734"</span>,
  <span class="k">"code"</span>:  <span class="s">"48512"</span>
}</div></div>""", glow="rgba(125,214,255,.2)"))

names.append(section("section_main", "SMENA", "Номера Telegram<br>за минуту", "Купил — вошёл — код уже в чате", ["60+ дней аренды", "Код прямо в чат", "Оплата криптой"], """
.phone{left:90px;top:0;width:330px;height:560px;border-radius:54px;background:#0B1124;border:9px solid #070B18;box-shadow:0 40px 80px rgba(3,8,30,.6),0 0 0 1.5px rgba(255,255,255,.12);padding:64px 20px 0}
.notch{position:absolute;left:50%;top:16px;width:96px;height:26px;border-radius:14px;background:#070B18;transform:translateX(-50%)}
.msg{border-radius:20px 20px 20px 6px;background:#18223D;padding:16px 18px;margin-bottom:12px;color:#fff;font-size:17px;font-weight:600}
.msg .s{display:block;margin-top:6px;font-family:'JetBrains Mono';color:#7DD6FF;font-size:19px}
.msg .big{display:block;margin-top:4px;font-family:'JetBrains Mono';font-weight:800;font-size:44px;letter-spacing:5px;color:#7DD6FF}
.btn{border-radius:14px;padding:13px;text-align:center;font-weight:700;font-size:17px;color:#fff;margin-bottom:10px}
.b1{background:#2AABEE}.b2{background:#2FB566}
.chipf{position:absolute;padding:12px 18px;border-radius:16px;font-weight:800;font-size:19px;color:#fff}
""", """<div class="glass phone"><div class="notch"></div>
<div class="msg">Номер готов<span class="s">+1 775 295 3734</span></div>
<div class="msg">Код входа в Telegram<span class="big">48512</span></div>
<div class="btn b1">Скопировать код</div><div class="btn b2">Купить ещё</div></div>
<div class="glass chipf" style="right:-40px;top:500px">⚡ за 10 секунд</div>
<div class="glass chipf" style="right:-10px;top:60px">🇺🇸 +1</div>""", h1size=84))
# Neutral cards for the static fallback: no personal data, still a full composition.
names.append(section("neutral_wallet", *WALLET_ARGS[1:6], WALLET_ARGS[6].split('<div class="glass pay">')[0] + """
<div class="glass pay"><div class="t muted">Способы пополнения</div>
<div class="m"><i class="g"></i>Crypto Bot <span>USDT · TON</span></div>
<div class="m"><i class="b"></i>xRocket <span>USDT</span></div>
<div class="m"><i class="t2"></i>TON <span>перевод</span></div></div>""", glow="rgba(27,162,122,.22)"))
names.append(section("neutral_profile", *PROFILE_ARGS[1:6], """
<div class="glass card"><div class="row1"><div class="ava"><svg viewBox="0 0 24 24" width="44" height="44"><circle cx="12" cy="8.5" r="4.2" fill="#fff"/><path d="M3.8 20.5c1-4.2 4.4-6.6 8.2-6.6s7.2 2.4 8.2 6.6" fill="#fff"/></svg></div><div><div class="nm">Ваш аккаунт</div><div class="id muted">баланс, номера и бонусы</div></div></div>
<div class="list"><div>📱 Мои номера <span>коды и продление</span></div><div>⭐️ Рефералы <span>5% с пополнений</span></div><div>📣 API <span>для реселлеров</span></div></div></div>"""))
json.dump({n: [1280, 720] for n in names}, open('list.json', 'w'))

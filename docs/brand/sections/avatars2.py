import json
HEAD = """<!doctype html><html><head><meta charset="utf-8"><style>*{margin:0;padding:0;box-sizing:border-box}
body{width:640px;height:640px;overflow:hidden;font-family:'Inter Display','Inter',sans-serif;-webkit-font-smoothing:antialiased}
.bg{width:640px;height:640px;background:linear-gradient(150deg,#1A2A5E 0%,#0E1838 100%);position:relative;overflow:hidden;color:#fff}
.flag{position:absolute;inset:0;background:repeating-linear-gradient(180deg,#B22234 0 49px,#fff 49px 98px)}
.canton{position:absolute;right:0;top:0;width:300px;height:343px;background:#3C3B6E;display:flex;flex-direction:column;justify-content:space-evenly;padding:8px 0}
.sr{display:flex;justify-content:space-evenly;padding:0 10px}.sr:nth-child(even){padding:0 34px}.sr span{font-size:22px;line-height:1}
%s</style></head><body><div class="bg">%s</div></body></html>"""
def stars(rows=9):
    return ''.join('<div class="sr">' + '<span>★</span>' * (6 if i % 2 == 0 else 5) + '</div>' for i in range(rows))
out = {}
def av(name, css, body):
    open(name + '.html', 'w').write(HEAD.replace('%s', css, 1).replace('%s', body, 1)); out[name] = [640, 640]

# 1: +1 on navy, flag rising from the bottom-right corner
av("ava_flag_plus", """
.flag{-webkit-mask:radial-gradient(120% 120% at 100% 100%,#000 0%,rgba(0,0,0,.55) 35%,transparent 70%)}
.canton{-webkit-mask:radial-gradient(140% 140% at 100% 100%,#000 10%,transparent 75%)}
.shade{position:absolute;inset:0;background:radial-gradient(42% 40% at 50% 52%,rgba(14,24,56,.75),transparent 75%)}
.plus{position:absolute;left:0;right:0;top:50%;transform:translate(-8px,-52%);text-align:center;font-weight:900;font-size:300px;letter-spacing:-16px;line-height:1;text-shadow:0 14px 50px rgba(8,16,40,.7)}
""", f'<div class="flag"><div class="canton">{stars()}</div></div><div class="shade"></div><div class="plus">+1</div>')

# 2: full flag fading to navy toward the bottom-left, brand tag in the center
av("ava_flag_tag", """
.flag{-webkit-mask:linear-gradient(200deg,#000 0%,rgba(0,0,0,.5) 40%,transparent 75%)}
.tag{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%) rotate(-6deg);padding:34px 46px;border-radius:40px;background:#E63946;font-weight:900;font-size:96px;letter-spacing:6px;padding-left:52px;box-shadow:0 30px 60px rgba(8,16,40,.55)}
""", f'<div class="flag"><div class="canton">{stars()}</div></div><div class="tag">SMENA</div>')

# 3: minimal navy, red brand tag with a small stars row
av("ava_navy_s", """
.glow{position:absolute;left:50%;top:50%;width:560px;height:560px;transform:translate(-50%,-50%);border-radius:50%;background:radial-gradient(circle,rgba(230,57,70,.22),transparent 65%)}
.box{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:300px;height:300px;border-radius:84px;background:#E63946;display:flex;align-items:center;justify-content:center;box-shadow:0 30px 70px rgba(8,16,40,.6)}
.box b{font-weight:900;font-size:230px;line-height:1;margin-top:-10px}
.st{position:absolute;left:0;right:0;top:118px;text-align:center;font-size:28px;letter-spacing:22px;padding-left:22px;opacity:.9}
.st2{top:auto;bottom:118px}
""", '<div class="glow"></div><div class="st">★ ★ ★</div><div class="box"><b>S</b></div><div class="st st2">★ ★ ★</div>')
json.dump(out, open('list.json', 'w'))

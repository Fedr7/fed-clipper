"""Builds Breachcut's logo, icon and wordmark on this PC (so the download stays tiny) and fetches the Anton font.
Run: python make_assets.py [assets_folder]. Safe to run again: it only makes what's missing."""
import math
import os
import sys
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "assets")
FONT_URLS = ("https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
             "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf")
S = 1024
PINK, WHITE = (255, 77, 109), (255, 255, 255)


def font(size):
    for f in (os.path.join(ASSETS, "Anton-Regular.ttf"), "impact.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def get_font():
    dst = os.path.join(ASSETS, "Anton-Regular.ttf")
    if os.path.exists(dst):
        return
    for url in FONT_URLS:
        try:
            data = urllib.request.urlopen(url, timeout=30).read()
            if len(data) > 50000:
                open(dst, "wb").write(data)
                return
        except Exception:
            continue


def bg():
    n = 256
    g = Image.new("RGB", (n, n))
    px = g.load()
    for y in range(n):
        for x in range(n):
            t = min(1, math.hypot(x - n * 0.32, y - n * 0.22) / n)
            c1, c2 = (52, 12, 30), (9, 6, 13)
            px[x, y] = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
    g = g.resize((S, S), Image.BICUBIC)
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], radius=225, fill=255)
    o = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    o.paste(g, (0, 0), m)
    return o


def glow(layer, radii):
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    for r, a in radii:
        b = layer.filter(ImageFilter.GaussianBlur(r))
        b.putalpha(b.getchannel("A").point(lambda v: min(255, int(v * a))))
        out = Image.alpha_composite(out, b)
    return Image.alpha_composite(out, layer)


def mark(with_bg=True):
    L = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(L)
    cx, cy, r = 512, 516, 318
    hexp = [(cx + r * math.cos(math.radians(60 * i - 90)), cy + r * math.sin(math.radians(60 * i - 90)))
            for i in range(6)]
    for i in range(6):
        d.line([hexp[i], hexp[(i + 1) % 6]], fill=WHITE + (255,), width=50)
    for p in hexp:
        d.ellipse([p[0] - 25, p[1] - 25, p[0] + 25, p[1] + 25], fill=WHITE + (255,))
    B = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(B).text((cx, cy + 6), "B", font=font(420), fill=PINK + (255,), anchor="mm")
    a, b = (170, 800), (860, 232)
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    px_, py_ = -uy, ux
    upper = Image.new("L", (S, S), 0)
    ImageDraw.Draw(upper).polygon([(a[0] - ux * 2000, a[1] - uy * 2000), (b[0] + ux * 2000, b[1] + uy * 2000),
                                   (b[0] + ux * 2000 + px_ * 3000, b[1] + uy * 2000 + py_ * 3000),
                                   (a[0] - ux * 2000 + px_ * 3000, a[1] - uy * 2000 + py_ * 3000)], fill=255)

    def split(im, s):
        up = im.copy()
        up.putalpha(ImageChops.multiply(im.getchannel("A"), upper))
        lo = im.copy()
        lo.putalpha(ImageChops.multiply(im.getchannel("A"), ImageChops.invert(upper)))
        up = up.transform((S, S), Image.AFFINE, (1, 0, ux * s, 0, 1, uy * s))
        lo = lo.transform((S, S), Image.AFFINE, (1, 0, -ux * s, 0, 1, -uy * s))
        return Image.alpha_composite(up, lo)
    gap = Image.new("L", (S, S), 0)
    ImageDraw.Draw(gap).line([a, b], fill=255, width=30)
    L.putalpha(ImageChops.subtract(L.getchannel("A"), gap))
    B.putalpha(ImageChops.subtract(B.getchannel("A"), gap))
    L, B = split(L, 10), split(B, 16)
    art = Image.alpha_composite(glow(L, ((38, 0.55), (10, 0.5))), glow(B, ((30, 0.8), (8, 0.6))))
    sl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sl).line([(a[0] - 20 * ux, a[1] - 20 * uy), (b[0] + 20 * ux, b[1] + 20 * uy)],
                            fill=(255, 214, 222, 255), width=9)
    art = Image.alpha_composite(art, glow(sl, ((44, 1.0), (16, 1.0), (5, 1.0))))
    return Image.alpha_composite(bg(), art) if with_bg else art


def build():
    os.makedirs(ASSETS, exist_ok=True)
    get_font()
    p = lambda n: os.path.join(ASSETS, n)
    need = [n for n in ("breachcut_icon_1024.png", "breachcut_mark.png", "breachcut.ico", "breachcut_wordmark.png")
            if not os.path.exists(p(n))]
    if not need:
        return
    icon = mark()
    icon.save(p("breachcut_icon_1024.png"))
    icon.save(p("breachcut.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    m = mark(with_bg=False)
    m.save(p("breachcut_mark.png"))
    mk = m.resize((180, 180), Image.LANCZOS)
    f = font(120)
    tw = ImageDraw.Draw(Image.new("L", (1, 1))).textlength("BREACHCUT", font=f)
    W = int(180 + 24 + tw + 20)
    wm = Image.new("RGBA", (W, 190), (0, 0, 0, 0))
    wm.paste(mk, (0, 5), mk)
    t = Image.new("RGBA", (W, 190), (0, 0, 0, 0))
    td = ImageDraw.Draw(t)
    td.text((204, 95), "BREACH", font=f, fill=(241, 237, 255, 255), anchor="lm")
    td.text((204 + td.textlength("BREACH", font=f), 95), "CUT", font=f, fill=PINK + (255,), anchor="lm")
    Image.alpha_composite(wm, t).save(p("breachcut_wordmark.png"))


if __name__ == "__main__":
    build()
    print("Breachcut artwork ready")

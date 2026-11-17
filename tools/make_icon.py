"""Disegna assets/icon.ico e icon.png: una fotocamera con l'obiettivo fatto di spicchi colorati (gli stili)."""

import os

from PIL import Image, ImageDraw

S = 1024  # si disegna grande e si riduce, per bordi morbidi
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# fondo: quadrato arrotondato con sfumatura verticale, come CleanCraftNas
bg = Image.new("RGBA", (S, S))
top, bottom = (45, 55, 72), (17, 24, 39)
for y in range(S):
    k = y / (S - 1)
    bg.paste(tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)) + (255,), (0, y, S, y + 1))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=255)
img.paste(bg, (0, 0), mask)
d = ImageDraw.Draw(img)

white = (243, 244, 246, 255)
# corpo della fotocamera e mirino
d.rounded_rectangle((200, 330, 824, 760), radius=70, fill=white)
d.rounded_rectangle((300, 270, 470, 360), radius=30, fill=white)
d.rounded_rectangle((690, 375, 760, 420), radius=18, fill=(96, 165, 250, 255))
# obiettivo: anello scuro con sei spicchi colorati, uno per stile
cx, cy, r = 512, 545, 168
d.ellipse((cx - r - 26, cy - r - 26, cx + r + 26, cy + r + 26), fill=(17, 24, 39, 255))
colors = [(96, 165, 250), (74, 222, 128), (251, 191, 36), (248, 113, 113), (192, 132, 252), (148, 163, 184)]
for i, color in enumerate(colors):
    start = -90 + i * 60
    d.pieslice((cx - r, cy - r, cx + r, cy + r), start, start + 60, fill=color + (255,))
d.ellipse((cx - 62, cy - 62, cx + 62, cy + 62), fill=(17, 24, 39, 255))
d.ellipse((cx - 26 - 18, cy - 26 - 18, cx - 26 + 18, cy - 26 + 18), fill=(255, 255, 255, 200))

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico")
os.makedirs(os.path.dirname(out), exist_ok=True)
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out.replace(".ico", ".png"))
print(out)

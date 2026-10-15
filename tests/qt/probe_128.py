"""One-off probe for #128: hash text, Stream Dock keys and widget grabs per OS.
Not a test. Usage: python tests/qt/probe_128.py <outdir>"""
import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
from tokitty import theme as T  # noqa: E402
from tokitty.customize import Customization  # noqa: E402
from tokitty.qt.widget import TokittyQtWidget  # noqa: E402
from tokitty.streamdock import render as R  # noqa: E402

fam = R.register_font()
tag = f"{sys.platform}_x{os.environ.get('QT_SCALE_FACTOR', '1')}"


def emit(name, img):
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    digest = hashlib.sha256(bytes(img.constBits())).hexdigest()[:12]
    img.save(str(out / f"{tag}_{name}.png"))
    print(f"PROBE {tag} {name} {img.width()}x{img.height()} {digest}")


H = {} if os.environ.get("ONLYCARD") else {"default": QFont.HintingPreference.PreferDefaultHinting,
     "none": QFont.HintingPreference.PreferNoHinting,
     "full": QFont.HintingPreference.PreferFullHinting,
     "vert": QFont.HintingPreference.PreferVerticalHinting}
for hn, hv in H.items():
    for dpr in (1, 2):
        img = QImage(200 * dpr, 40 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(30, 30, 30))
        p = QPainter(img)
        f = QFont(fam)
        f.setPixelSize(12)
        f.setBold(True)
        f.setHintingPreference(hv)
        p.setFont(f)
        p.setPen(QColor(235, 235, 235))
        p.drawText(QRectF(4, 4, 190, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "SESSION 42% resets 3h")
        p.end()
        emit(f"text_{hn}_dpr{dpr}", img)

import base64  # noqa: E402

for name, uri in [] if os.environ.get("ONLYCARD") else [("status", R.status_key("hello")), ("usage", R.usage_key(42, 77)),
                  ("decision", R.decision_key("allow"))]:
    emit(f"key_{name}", QImage.fromData(base64.b64decode(uri.split(",", 1)[1])))


def fonts(self):
    def make(px, bold):
        f = QFont(fam)
        f.setPixelSize(px)
        f.setBold(bold)
        f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        f.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
        return f
    return make(12, True), make(11, True), make(11, False)


TokittyQtWidget._font_set = fonts
for name, home, opacity in [("card", "inside", 100), ("card_right", "inside_right", 100),
                            ("card_top", "on_top", 100), ("card_low", "inside", 20)]:
    theme = T.Theme()
    if opacity != 100:
        import dataclasses
        theme = dataclasses.replace(theme, card=dataclasses.replace(theme.card, opacity=opacity))
    w = TokittyQtWidget(theme, [Customization(cat_home=home, label="work")], 1)
    w.anim.stop()
    w.panes[0].set_appearance(label="work")
    w.panes[0].render("working", 42, 67, "resets in 2h", "Mon", "", None, "open Claude Code", False)
    emit(name, w.grab().toImage())
_orig_font = R._font


def _fixed_font(size):
    f = _orig_font(size)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    f.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
    return f


R._font = _fixed_font
for fn in (R._session_key, R._usage_key, R._text_lines):
    fn.cache_clear()
for name, uri in [("status", R.status_key("hello")), ("usage", R.usage_key(42, 77)),
                  ("decision", R.decision_key("allow"))]:
    emit(f"keyfixed_{name}", QImage.fromData(base64.b64decode(uri.split(",", 1)[1])))
print("PROBE done")

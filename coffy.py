#!/usr/bin/env python3
"""KDE system-tray app for the coffee-machine plug.

Controls the plug exclusively through the TP-Link Cloud V2 API
(local KLAP is broken on this firmware, see notes in repo).
"""
import asyncio
import sys
import traceback
from enum import Enum

import keyring
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from tplink_cloud import CloudError, MFARequired, TPLinkCloud

try:
    from config import PLUG_DEVICE_ID
except ImportError:
    raise SystemExit(
        "Missing config.py. Create it with: PLUG_DEVICE_ID = \"<your plug's deviceId>\""
    )

SERVICE = "coffy"
POLL_INTERVAL_MS = 30_000


class State(Enum):
    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"
    ON = "on"
    OFF = "off"


def make_cloud() -> TPLinkCloud:
    email = keyring.get_password(SERVICE, "tplink_email")
    password = keyring.get_password(SERVICE, "tplink_password")
    if not email or not password:
        raise RuntimeError(
            "TP-Link credentials not in KDE Wallet. Run set_credentials.py."
        )
    refresh = keyring.get_password(SERVICE, "tplink_refresh_token")
    return TPLinkCloud(
        email=email,
        password=password,
        refresh_token=refresh,
        on_refresh_token_change=lambda t: keyring.set_password(
            SERVICE, "tplink_refresh_token", t
        ),
    )


async def cloud_fetch_state() -> bool:
    return await make_cloud().get_relay_state(PLUG_DEVICE_ID)


async def cloud_set_state(on: bool) -> bool:
    return await make_cloud().set_relay_state(PLUG_DEVICE_ID, on)


class AsyncWorker(QThread):
    done = pyqtSignal(object, object)  # (result, error)

    def __init__(self, coro_factory):
        super().__init__()
        self._factory = coro_factory

    def run(self):
        try:
            self.done.emit(asyncio.run(self._factory()), None)
        except Exception as e:
            traceback.print_exc()
            self.done.emit(None, e)


def make_icon(state: State) -> QIcon:
    size = 128
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    cup_fill = QColor("#f1f5f9")
    cup_stroke = (
        QColor("#94a3b8") if state is State.DISCONNECTED else QColor("#475569")
    )

    if state is State.ON:
        p.setPen(QPen(QColor("#94a3b8"), 12, cap=Qt.PenCapStyle.RoundCap))
        for cx in (44, 64, 84):
            path = QPainterPath()
            path.moveTo(cx, 4)
            path.cubicTo(cx + 10, 10, cx - 10, 18, cx, 26)
            p.drawPath(path)

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(cup_stroke, 20, cap=Qt.PenCapStyle.FlatCap))
    p.drawArc(82, 42, 30, 46, -90 * 16, 180 * 16)
    p.setPen(QPen(cup_fill, 12, cap=Qt.PenCapStyle.FlatCap))
    p.drawArc(82, 42, 30, 46, -90 * 16, 180 * 16)

    cup = QPainterPath()
    cup.moveTo(20, 36)
    cup.quadTo(20, 32, 24, 32)
    cup.lineTo(84, 32)
    cup.quadTo(88, 32, 88, 36)
    cup.lineTo(82, 90)
    cup.quadTo(80, 98, 70, 98)
    cup.lineTo(38, 98)
    cup.quadTo(28, 98, 26, 90)
    cup.closeSubpath()
    p.setBrush(QBrush(cup_fill))
    p.setPen(QPen(cup_stroke, 5))
    p.drawPath(cup)

    if state is not State.DISCONNECTED:
        p.setBrush(QBrush(cup_fill))
        p.setPen(QPen(cup_stroke, 4))
        p.drawRoundedRect(14, 104, 88, 12, 6, 6)

    if state is State.DISCONNECTED:
        p.setPen(QPen(QColor("#dc2626"), 10, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(28, 40, 80, 92)
        p.drawLine(80, 40, 28, 92)

    p.end()
    return QIcon(pix)


class Tray:
    def __init__(self, app: QApplication):
        self.app = app
        self.state: State = State.UNKNOWN
        self.busy = False
        self._workers: list[AsyncWorker] = []

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(make_icon(State.UNKNOWN))
        self.tray.setToolTip("Coffee plug: connecting…")

        menu = QMenu()
        self.act_on = menu.addAction("Turn ON")
        self.act_off = menu.addAction("Turn OFF")
        menu.addSeparator()
        self.act_refresh = menu.addAction("Refresh")
        menu.addSeparator()
        self.act_quit = menu.addAction("Quit")

        self.act_on.triggered.connect(lambda: self._do(cloud_set_state, True))
        self.act_off.triggered.connect(lambda: self._do(cloud_set_state, False))
        self.act_refresh.triggered.connect(self.refresh)
        self.act_quit.triggered.connect(self.app.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_INTERVAL_MS)

        QTimer.singleShot(0, self.refresh)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle()

    def _toggle(self):
        if self.state is State.ON:
            self._do(cloud_set_state, False)
        elif self.state is State.OFF:
            self._do(cloud_set_state, True)
        else:
            self.refresh()

    def refresh(self):
        self._do(cloud_fetch_state)

    def _do(self, coro_fn, *args):
        if self.busy:
            return
        self.busy = True
        worker = AsyncWorker(lambda: coro_fn(*args))
        worker.done.connect(self._on_result)
        worker.finished.connect(lambda w=worker: self._workers.remove(w))
        self._workers.append(worker)
        worker.start()

    def _on_result(self, result, error):
        self.busy = False
        if error is not None:
            self.state = State.DISCONNECTED
            self.tray.setIcon(make_icon(self.state))
            tip = "Coffee plug: disconnected"
            if isinstance(error, MFARequired):
                tip += "\nMFA required — re-run cloud_probe.py interactively"
            elif isinstance(error, CloudError):
                tip += f"\n{error}"
            else:
                tip += f"\n{type(error).__name__}: {error}"
            self.tray.setToolTip(tip)
            return
        self.state = State.ON if result else State.OFF
        self.tray.setIcon(make_icon(self.state))
        self.tray.setToolTip(f"Coffee plug: {'ON' if result else 'OFF'}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray not available on this desktop.", file=sys.stderr)
        return 1
    _ = Tray(app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

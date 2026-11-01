'''
Shows the 2021 game client as "Echo": renames its window and gives it the Echo icon
(Windows only). Done from outside the client so the downloaded files stay untouched.
'''
import ctypes
import os
import sys
import threading
import time

TITLE = 'Echo'


def _icon_path() -> str:
    # bundled builds unpack data files to sys._MEIPASS; source runs keep Icon.ico in Source/
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'Icon.ico')


def _windows_of(pid: int) -> list[int]:
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def _brand_loop(pid: int, alive) -> None:
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    path = _icon_path()
    icons = {}
    if os.path.exists(path):
        for which, size in ((0, 16), (1, 32)):  # ICON_SMALL, ICON_BIG
            icons[which] = user32.LoadImageW(None, path, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
    buf = ctypes.create_unicode_buffer(256)
    branded: set[int] = set()
    while alive():
        for hwnd in _windows_of(pid):
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value != TITLE:
                user32.SetWindowTextW(hwnd, TITLE)
            if hwnd not in branded:
                for which, handle in icons.items():
                    if handle:
                        user32.SendMessageW(hwnd, 0x80, which, handle)  # WM_SETICON
                branded.add(hwnd)
        # the client may reset its title while loading, so keep checking
        time.sleep(2 if branded else 0.25)


def brand(process) -> None:
    '''Brands the windows of a running client `subprocess.Popen` until it exits.'''
    if sys.platform != 'win32':
        return
    threading.Thread(
        target=_brand_loop, args=(process.pid, lambda: process.poll() is None), daemon=True,
    ).start()

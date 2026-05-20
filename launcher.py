"""
Окно «Запуск / Стоп» для VK-бота ГЕОСПАЙДЕР (как у Telegram-версии).
Запуск: Запустить_VK_бота.bat или python launcher.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog

from env_utils import ENV_FILE, read_env_value, write_env_value

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PID_FILE = DATA_DIR / "bot.pid"
LOG_FILE = DATA_DIR / "bot.log"

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def python_executable() -> Path:
    venv_python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def is_running() -> bool:
    pid = read_pid()
    if pid is None:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tail_log(max_lines: int = 80) -> str:
    if not LOG_FILE.exists():
        return ""
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def ensure_venv_and_deps(parent: tk.Misc | None = None) -> bool:
    py = python_executable()
    if not (BASE_DIR / ".venv" / "Scripts" / "python.exe").exists() and str(py) == str(
        Path(sys.executable)
    ):
        messagebox.showerror(
            "Нет виртуального окружения",
            "Сначала выполните в папке бота:\n"
            "  python -m venv .venv\n"
            "  .venv\\Scripts\\activate\n"
            "  pip install -r requirements.txt",
            parent=parent,
        )
        return False

    try:
        r = subprocess.run(
            [str(py), "-c", "import httpx, dotenv"],
            cwd=str(BASE_DIR),
            capture_output=True,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    if parent:
        messagebox.showinfo(
            "Зависимости",
            "Устанавливаю пакеты из requirements.txt…",
            parent=parent,
        )
    req = BASE_DIR / "requirements.txt"
    inst = subprocess.run(
        [str(py), "-m", "pip", "install", "-r", str(req)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if inst.returncode != 0:
        tail = (inst.stderr or inst.stdout or "")[-400:]
        messagebox.showerror(
            "pip",
            f"Не удалось установить зависимости.\n\n{tail}",
            parent=parent,
        )
        return False
    return True


def run_network_check() -> tuple[int, str]:
    """Возвращает (код выхода, текст stdout+stderr для окна ошибки)."""
    python = str(python_executable())
    result = subprocess.run(
        [python, str(BASE_DIR / "network_check.py")],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    out = (result.stdout or "") + (result.stderr or "")
    if out.strip():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as log:
            log.write(out)
            if not out.endswith("\n"):
                log.write("\n")
    return result.returncode, out.strip()


def configure_vk_dialog(parent: tk.Misc | None) -> None:
    token = simpledialog.askstring(
        "VK_GROUP_TOKEN",
        "Ключ доступа сообщества (с правом «Сообщения сообщества»):",
        initialvalue=read_env_value("VK_GROUP_TOKEN") or "",
        show="*",
        parent=parent,
    )
    if token is None:
        return
    gid = simpledialog.askstring(
        "VK_GROUP_ID",
        "Числовой id группы (из club123 → 123):",
        initialvalue=read_env_value("VK_GROUP_ID") or "",
        parent=parent,
    )
    if gid is None:
        return
    write_env_value("VK_GROUP_TOKEN", token.strip())
    write_env_value("VK_GROUP_ID", gid.strip())
    messagebox.showinfo("Сохранено", "VK_GROUP_TOKEN и VK_GROUP_ID записаны в .env", parent=parent)


def start_bot() -> None:
    if is_running():
        messagebox.showinfo("ГЕОСПАЙДЕР VK", "Бот уже запущен.")
        return

    # После сбоя процесс мог исчезнуть, а bot.pid остался — снимаем «зависший» pid.
    if PID_FILE.exists() and read_pid() is not None and not is_running():
        PID_FILE.unlink(missing_ok=True)

    if not ENV_FILE.exists():
        messagebox.showerror(
            "ГЕОСПАЙДЕР VK",
            "Нет файла .env.\nСкопируйте .env.example в .env или настройте через «Настройки VK».",
        )
        return

    if not ensure_venv_and_deps(None):
        return

    check_rc, check_out = run_network_check()
    if check_rc != 0:
        tail = check_out[-900:] if check_out else ""
        proceed = messagebox.askyesno(
            "Проверка не прошла",
            "Сеть или ключ VK не прошли проверку.\n\n"
            "Чаще всего: ошибка 27 — ключ отозван или не от этого сообщества; "
            "создайте новый ключ в сообществе → Работа с API.\n\n"
            f"{tail}\n\n"
            "Запустить бота всё равно?",
            default="no",
        )
        if not proceed:
            return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    log_handle.write("\n--- запуск VK-бота ---\n")
    log_handle.flush()

    python = str(python_executable())
    creationflags = CREATE_NEW_PROCESS_GROUP
    if os.name == "nt":
        creationflags |= CREATE_NO_WINDOW

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen(
        [python, str(BASE_DIR / "bot.py")],
        cwd=str(BASE_DIR),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        env=env,
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    log_handle.close()

    time.sleep(1.2)
    rc = process.poll()
    if rc is not None:
        tail = tail_log(25)
        messagebox.showerror(
            "Бот сразу завершился",
            f"Процесс завершился с кодом {rc} (часто это ошибка VK или .env).\n\n"
            "Сделайте: «Стоп» (если есть), затем **Проверка_VK.bat** или "
            "`python network_check.py` в папке бота.\n\n"
            "Последние строки журнала:\n"
            f"{tail[-1200:]}",
        )
        PID_FILE.unlink(missing_ok=True)


def stop_bot() -> None:
    pid = read_pid()
    if pid is None or not is_running():
        PID_FILE.unlink(missing_ok=True)
        messagebox.showinfo("ГЕОСПАЙДЕР VK", "Бот не запущен.")
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    PID_FILE.unlink(missing_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write("--- остановка VK-бота ---\n")


class LauncherApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("ГЕОСПАЙДЕР — VK-бот")
        self.root.geometry("560x420")
        self.root.minsize(460, 340)

        tk.Label(
            self.root,
            text="Уведомления о станциях через сообщество VK",
            font=("Segoe UI", 11),
        ).pack(pady=(12, 4))

        self.status_label = tk.Label(self.root, text="", font=("Segoe UI", 10, "bold"))
        self.status_label.pack(pady=(0, 2))

        self.info_label = tk.Label(
            self.root,
            text="",
            font=("Segoe UI", 9),
            fg="#555555",
            wraplength=520,
        )
        self.info_label.pack(pady=(0, 8))

        buttons = tk.Frame(self.root)
        buttons.pack(pady=4)

        tk.Button(
            buttons,
            text="▶  Запуск",
            width=12,
            height=2,
            bg="#2e7d32",
            fg="white",
            command=self.on_start,
        ).grid(row=0, column=0, padx=4)

        tk.Button(
            buttons,
            text="■  Стоп",
            width=12,
            height=2,
            bg="#c62828",
            fg="white",
            command=self.on_stop,
        ).grid(row=0, column=1, padx=4)

        tk.Button(
            buttons,
            text="Настройки VK",
            width=12,
            height=2,
            command=self.on_vk_settings,
        ).grid(row=0, column=2, padx=4)

        tk.Label(self.root, text="Журнал:", anchor="w").pack(fill="x", padx=12, pady=(8, 0))

        self.log_box = scrolledtext.ScrolledText(
            self.root,
            height=12,
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_ui()

    def on_vk_settings(self) -> None:
        configure_vk_dialog(self.root)
        self.refresh_ui()

    def on_start(self) -> None:
        try:
            start_bot()
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
        self.refresh_ui()

    def on_stop(self) -> None:
        try:
            stop_bot()
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
        self.refresh_ui()

    def on_close(self) -> None:
        if is_running():
            if messagebox.askyesno(
                "Выход",
                "Бот работает в фоне.\nЗакрыть окно лаунчера?",
            ):
                self.root.destroy()
        else:
            self.root.destroy()

    def refresh_ui(self) -> None:
        running = is_running()
        if running:
            self.status_label.config(
                text=f"Запущен (PID {read_pid()})",
                fg="#2e7d32",
            )
        else:
            self.status_label.config(text="Остановлен", fg="#c62828")

        gid = read_env_value("VK_GROUP_ID") or "не задан"
        tok = read_env_value("VK_GROUP_TOKEN")
        tok_ok = "токен задан" if tok else "токен не задан"
        self.info_label.config(text=f"VK_GROUP_ID: {gid} · {tok_ok}")

        text = tail_log()
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.insert(tk.END, text or "Пусто. Нажмите «Запуск».")
        self.log_box.config(state="disabled")
        self.log_box.see(tk.END)

        self.root.after(2000, self.refresh_ui)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    LauncherApp().run()

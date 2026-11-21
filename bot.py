
import asyncio
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path

import instaloader
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================================================
# CONFIG
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

DATA_FILE = Path("database.json")

DEFAULT_INTERVAL = 60
MIN_INTERVAL = 5

# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("InstagramMonitor")



# Render HTTP health endpoint
class HealthHandler(BaseHTTPRequestHandler):

    def _send_response(self, body, content_type, head_only=False):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_response(
                b'{"status":"ok"}',
                "application/json"
            )

        elif self.path == "/":
            self._send_response(
                b"Instagram monitor bot is running. Health: /health",
                "text/plain; charset=utf-8"
            )

        else:
            self.send_error(404)

    def do_HEAD(self):
        if self.path == "/health":
            self._send_response(
                b'{"status":"ok"}',
                "application/json",
                head_only=True
            )

        elif self.path == "/":
            self._send_response(
                b"Instagram monitor bot is running. Health: /health",
                "text/plain; charset=utf-8",
                head_only=True
            )

        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        logger.info("HTTP: " + fmt, *args)


# ==================================================
# DATABASE
# ==================================================

def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            data.setdefault("accounts", {})
            data.setdefault("logs", [])
            data.setdefault("interval", DEFAULT_INTERVAL)

            return data

        except Exception as e:
            logger.error(f"Database read error: {e}")

    return {
        "accounts": {},
        "logs": [],
        "interval": DEFAULT_INTERVAL,
    }


data = load_data()


def save_data():
    temp_file = DATA_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(temp_file, DATA_FILE)


def add_log(username, metric, old, new):
    data["logs"].append({
        "time": datetime.now().astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "username": username,
        "metric": metric,
        "old": old,
        "new": new,
    })

    save_data()


# ==================================================
# INSTAGRAM
# ==================================================

instagram = instaloader.Instaloader(
    quiet=True,
    max_connection_attempts=1,
)


def normalize_username(username):
    username = username.strip()
    username = username.replace("https://www.instagram.com/", "")
    username = username.replace("http://www.instagram.com/", "")
    username = username.split("?")[0].strip("/").strip()

    if username.startswith("@"):
        username = username[1:]

    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
        raise ValueError("Invalid Instagram username")

    return username


def get_stats(username):
    profile = instaloader.Profile.from_username(
        instagram.context,
        username,
    )

    return {
        "followers": profile.followers,
        "following": profile.followees,
    }


# ==================================================
# KEYBOARDS
# ==================================================

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("👥 مدیریت اکانت‌های تحت نظر")],
        [KeyboardButton("⏱ تعیین تایم نظارت")],
        [KeyboardButton("📊 تعداد فالوور و فالووینگ")],
        [KeyboardButton("📜 لاگ کلی")],
    ],
    resize_keyboard=True,
)

ACCOUNT_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ افزودن اکانت")],
        [KeyboardButton("➖ حذف اکانت خاص")],
        [KeyboardButton("🗑 حذف همه اکانت‌ها")],
        [KeyboardButton("🔙 بازگشت به پنل")],
    ],
    resize_keyboard=True,
)


def account_delete_keyboard():
    buttons = [
        [KeyboardButton("@" + username)]
        for username in data["accounts"]
    ]

    buttons.append([KeyboardButton("🔙 بازگشت به مدیریت")])

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
    )


def confirmation_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✅ بله، مطمئنم")],
            [KeyboardButton("❌ خیر، لغو کن")],
        ],
        resize_keyboard=True,
    )


# ==================================================
# ADMIN CHECK
# ==================================================

def is_admin(update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):
        return

    context.user_data.clear()

    await update.message.reply_text(
        "سلام مدیر عزیز! 👋\n\n"
        "به پنل مانیتورینگ اینستاگرام خوش آمدی.\n"
        "یکی از گزینه‌های زیر را انتخاب کن:",
        reply_markup=MAIN_MENU,
    )


# ==================================================
# ACCOUNT MANAGEMENT
# ==================================================

async def add_account(update, context):

    context.user_data["state"] = "add_account"

    await update.message.reply_text(
        "آیدی اکانت اینستاگرام را ارسال کن.\n\n"
        "مثال: instagram",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🔙 بازگشت به مدیریت")]],
            resize_keyboard=True,
        ),
    )


async def remove_account(update, context):

    if not data["accounts"]:
        await update.message.reply_text(
            "هنوز هیچ اکانتی ثبت نشده است.",
            reply_markup=ACCOUNT_MENU,
        )
        return

    context.user_data["state"] = "select_delete"

    await update.message.reply_text(
        "اکانتی را که می‌خواهی حذف کنی انتخاب کن:",
        reply_markup=account_delete_keyboard(),
    )


async def delete_all(update, context):

    if not data["accounts"]:
        await update.message.reply_text(
            "لیست اکانت‌ها خالی است.",
            reply_markup=ACCOUNT_MENU,
        )
        return

    context.user_data["state"] = "confirm_delete_all"

    await update.message.reply_text(
        f"⚠️ مطمئنی می‌خواهی تمام "
        f"{len(data['accounts'])} اکانت را حذف کنی؟",
        reply_markup=confirmation_keyboard(),
    )


# ==================================================
# MONITORING
# ==================================================

async def monitor_loop(app):

    logger.info("Instagram monitor started.")

    while True:

        try:
            interval = max(
                MIN_INTERVAL,
                int(data["interval"]),
            )

            await asyncio.sleep(interval)

            for username in list(data["accounts"].keys()):

                if username not in data["accounts"]:
                    continue

                try:
                    current = await asyncio.to_thread(
                        get_stats,
                        username,
                    )

                    previous = data["accounts"][username]

                    changes = []

                    # Followers
                    if current["followers"] != previous["followers"]:

                        old = previous["followers"]
                        new = current["followers"]

                        add_log(
                            username,
                            "فالوور",
                            old,
                            new,
                        )

                        changes.append(
                            f"👥 تعداد فالوورها تغییر کرد:\n"
                            f"{old:,} ➜ {new:,}"
                        )

                    # Following
                    if current["following"] != previous["following"]:

                        old = previous["following"]
                        new = current["following"]

                        add_log(
                            username,
                            "فالووینگ",
                            old,
                            new,
                        )

                        changes.append(
                            f"➡️ تعداد فالووینگ‌ها تغییر کرد:\n"
                            f"{old:,} ➜ {new:,}"
                        )

                    # Update baseline
                    data["accounts"][username] = current
                    save_data()

                    # Notify admin
                    if changes:

                        message = (
                            f"🚨 تغییر در اکانت @{username}\n\n"
                            + "\n\n".join(changes)
                            + "\n\n"
                            + f"👥 فالوور فعلی: "
                            f"{current['followers']:,}\n"
                            + f"➡️ فالووینگ فعلی: "
                            f"{current['following']:,}\n\n"
                            + f"🕒 زمان تشخیص: "
                            f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
                        )

                        await app.bot.send_message(
                            chat_id=ADMIN_ID,
                            text=message,
                        )

                        logger.info(
                            f"Change detected: @{username}"
                        )

                except Exception as e:

                    logger.error(
                        f"Monitoring @{username} failed: {e}"
                    )

        except asyncio.CancelledError:
            raise

        except Exception as e:
            logger.exception(f"Monitor loop error: {e}")
            await asyncio.sleep(30)


# ==================================================
# FOLLOWERS / FOLLOWING REPORT
# ==================================================

async def show_all_stats(update, context):

    if not data["accounts"]:
        await update.message.reply_text(
            "هیچ اکانتی برای نمایش وجود ندارد.",
            reply_markup=MAIN_MENU,
        )
        return

    await update.message.reply_text(
        "⏳ در حال دریافت اطلاعات اکانت‌ها..."
    )

    lines = ["📊 گزارش تعداد فالوور و فالووینگ\n"]

    for username in list(data["accounts"].keys()):

        try:
            stats = await asyncio.to_thread(
                get_stats,
                username,
            )

            lines.append(
                f"━━━━━━━━━━━━━━\n"
                f"👤 @{username}\n"
                f"👥 Followers: {stats['followers']:,}\n"
                f"➡️ Following: {stats['following']:,}"
            )

        except Exception as e:

            logger.error(f"Stats @{username}: {e}")

            lines.append(
                f"━━━━━━━━━━━━━━\n"
                f"👤 @{username}\n"
                f"⚠️ دریافت اطلاعات ناموفق بود."
            )

    message = "\n".join(lines)

    for i in range(0, len(message), 4000):
        await update.message.reply_text(
            message[i:i + 4000],
            reply_markup=MAIN_MENU if i + 4000 >= len(message) else None,
        )


# ==================================================
# LOG REPORT
# ==================================================

async def show_logs(update, context):

    logs = data["logs"]

    if not logs:
        await update.message.reply_text(
            "هنوز هیچ تغییری ثبت نشده است.",
            reply_markup=MAIN_MENU,
        )
        return

    # Build one complete entry per change, then pack entries into
    # Telegram-safe messages without splitting an entry.
    entries = []

    for index, item in enumerate(logs, 1):
        direction = (
            "📈 افزایش"
            if item["new"] > item["old"]
            else "📉 کاهش"
        )

        entries.append(
            f"{index}. @{item['username']}\n"
            f"نوع: {item['metric']}\n"
            f"تغییر: {item['old']:,} ➜ {item['new']:,}\n"
            f"{direction}\n"
            f"زمان: {item['time']}"
        )

    header = (
        "📜 تاریخچه تغییرات اینستاگرام\n"
        "ترتیب: قدیمی به جدید"
    )

    # Stay comfortably below Telegram's message length limit.
    max_length = 3800
    messages = []
    current = header

    for entry in entries:
        candidate = current + "\n\n" + entry

        if len(candidate) > max_length and current != header:
            messages.append(current)
            current = header + "\n\n" + entry
        else:
            current = candidate

        # A single unusually long entry is still sent separately.
        if len(current) > max_length:
            messages.append(current)
            current = header

    if current != header:
        messages.append(current)

    for index, message in enumerate(messages):
        is_last = index == len(messages) - 1

        await update.message.reply_text(
            message,
            reply_markup=MAIN_MENU if is_last else None,
        )


# ==================================================
# MAIN MESSAGE HANDLER
# ==================================================

async def handle_message(update: Update, context):

    if not is_admin(update):
        return

    text = update.message.text.strip()
    state = context.user_data.get("state")

    # ------------------------------
    # Add account
    # ------------------------------

    if state == "add_account":

        if text == "🔙 بازگشت به مدیریت":
            context.user_data.clear()
            await update.message.reply_text(
                "مدیریت اکانت‌ها:",
                reply_markup=ACCOUNT_MENU,
            )
            return

        try:
            username = normalize_username(text)

            if username in data["accounts"]:
                await update.message.reply_text(
                    "این اکانت قبلاً اضافه شده است.",
                    reply_markup=ACCOUNT_MENU,
                )
                context.user_data.clear()
                return

            await update.message.reply_text(
                "⏳ در حال بررسی اکانت..."
            )

            stats = await asyncio.to_thread(
                get_stats,
                username,
            )

            data["accounts"][username] = stats
            save_data()

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ اکانت با موفقیت اضافه شد!\n\n"
                f"👤 @{username}\n"
                f"👥 Followers: {stats['followers']:,}\n"
                f"➡️ Following: {stats['following']:,}",
                reply_markup=ACCOUNT_MENU,
            )

        except Exception as e:

            logger.error(f"Add account error: {e}")

            context.user_data.clear()

            await update.message.reply_text(
                "❌ دریافت اطلاعات اکانت ناموفق بود.\n"
                "آیدی را بررسی کن. اکانت باید قابل دسترسی باشد.",
                reply_markup=ACCOUNT_MENU,
            )

        return

    # ------------------------------
    # Select account to delete
    # ------------------------------

    if state == "select_delete":

        if text == "🔙 بازگشت به مدیریت":
            context.user_data.clear()
            await update.message.reply_text(
                "مدیریت اکانت‌ها:",
                reply_markup=ACCOUNT_MENU,
            )
            return

        try:
            username = normalize_username(text)

            if username not in data["accounts"]:
                await update.message.reply_text(
                    "این اکانت در لیست وجود ندارد.",
                    reply_markup=account_delete_keyboard(),
                )
                return

            context.user_data["pending_delete"] = username
            context.user_data["state"] = "confirm_delete"

            await update.message.reply_text(
                f"آیا از حذف @{username} مطمئنی؟",
                reply_markup=confirmation_keyboard(),
            )

        except ValueError:
            await update.message.reply_text(
                "اکانت معتبر نیست.",
                reply_markup=account_delete_keyboard(),
            )

        return

    # ------------------------------
    # Confirm deletion
    # ------------------------------

    if state in ("confirm_delete", "confirm_delete_all"):

        if text == "❌ خیر، لغو کن":

            context.user_data.clear()

            await update.message.reply_text(
                "عملیات لغو شد.",
                reply_markup=ACCOUNT_MENU,
            )
            return

        if text != "✅ بله، مطمئنم":

            await update.message.reply_text(
                "لطفاً یکی از گزینه‌های تأیید یا لغو را انتخاب کن.",
                reply_markup=confirmation_keyboard(),
            )
            return

        if state == "confirm_delete":

            username = context.user_data["pending_delete"]

            data["accounts"].pop(username, None)
            save_data()

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ اکانت @{username} حذف شد.",
                reply_markup=ACCOUNT_MENU,
            )

        else:

            count = len(data["accounts"])

            data["accounts"].clear()
            save_data()

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ تمام {count} اکانت حذف شدند.",
                reply_markup=ACCOUNT_MENU,
            )

        return

    # ------------------------------
    # Set monitoring interval
    # ------------------------------

    if state == "set_interval":

        if text == "🔙 بازگشت به پنل":
            context.user_data.clear()
            await update.message.reply_text(
                "پنل اصلی:",
                reply_markup=MAIN_MENU,
            )
            return

        if not text.isdigit():

            await update.message.reply_text(
                "لطفاً فقط یک عدد صحیح برحسب ثانیه ارسال کن."
            )
            return

        seconds = int(text)

        if seconds < MIN_INTERVAL:

            await update.message.reply_text(
                f"حداقل فاصله مجاز {MIN_INTERVAL} ثانیه است.\n"
                "برای جلوگیری از محدودیت اینستاگرام، "
                "فواصل کوتاه توصیه نمی‌شوند."
            )
            return

        data["interval"] = seconds
        save_data()

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ فاصله بررسی روی {seconds} ثانیه تنظیم شد.",
            reply_markup=MAIN_MENU,
        )
        return

    # ------------------------------
    # Main menu
    # ------------------------------

    if text == "👥 مدیریت اکانت‌های تحت نظر":

        context.user_data.clear()

        await update.message.reply_text(
            f"مدیریت اکانت‌ها\n\n"
            f"تعداد اکانت‌های ثبت‌شده: {len(data['accounts'])}",
            reply_markup=ACCOUNT_MENU,
        )

    elif text == "➕ افزودن اکانت":
        await add_account(update, context)

    elif text == "➖ حذف اکانت خاص":
        await remove_account(update, context)

    elif text == "🗑 حذف همه اکانت‌ها":
        await delete_all(update, context)

    elif text == "🔙 بازگشت به پنل":

        context.user_data.clear()

        await update.message.reply_text(
            "پنل اصلی:",
            reply_markup=MAIN_MENU,
        )

    elif text == "🔙 بازگشت به مدیریت":

        context.user_data.clear()

        await update.message.reply_text(
            "مدیریت اکانت‌ها:",
            reply_markup=ACCOUNT_MENU,
        )

    elif text == "⏱ تعیین تایم نظارت":

        context.user_data["state"] = "set_interval"

        await update.message.reply_text(
            f"⏱ فاصله فعلی: {data['interval']} ثانیه\n\n"
            "فاصله جدید را برحسب ثانیه ارسال کن.\n"
            f"حداقل مقدار: {MIN_INTERVAL}",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🔙 بازگشت به پنل")]],
                resize_keyboard=True
            )
        )

    elif text == "📊 تعداد فالوور و فالووینگ":

        context.user_data.clear()
        await show_all_stats(update, context)

    elif text == "📜 لاگ کلی":

        context.user_data.clear()
        await show_logs(update, context)

    else:

        await update.message.reply_text(
            "از دکمه‌های پنل استفاده کن.",
            reply_markup=MAIN_MENU,
        )


# ==================================================
# APPLICATION
# ==================================================

monitor_task = None


async def post_init(app):

    global monitor_task

    monitor_task = asyncio.create_task(
        monitor_loop(app)
    )


async def post_shutdown(app):

    global monitor_task

    if monitor_task:
        monitor_task.cancel()

        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


def main():

    if BOT_TOKEN == "YOUR_BOT_TOKEN":
        raise ValueError("BOT_TOKEN is not configured!")

    if ADMIN_ID == 123456789:
        raise ValueError("ADMIN_ID is not configured!")

    # Render web services must listen on 0.0.0.0 and the PORT environment variable.
    health_server = start_health_server()
    save_data()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Telegram bot is starting...")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        health_server.shutdown()
        health_server.server_close()


if __name__ == "__main__":
    main()

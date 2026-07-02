import os
import sqlite3
import re
import json
import asyncio
import logging
import sys
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, ChatMember, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
BOT_TOKEN = "8671613935:AAFsG7gbKFjZ2VRdKQaJZnGTrut__K9M59w"
BOT_USERNAME = "Secureblebot"
ADMIN_IDS = [7691071175, 7913633925]
MM_USERNAME = "shuify"
VOUCH_FORWARD_CHANNEL_ID = -1003711319131

# ─── FAKE HTTP SERVER FOR RENDER ─────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        pass

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f" Fake HTTP server running on port {port}")
    server.serve_forever()

# ─── DATABASE SETUP ─────────────────────────────────────────────────────────
DB_PATH = "mm_3bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS deals (
        deal_id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        deal_number INTEGER,
        deal_terms TEXT,
        buyer_id INTEGER,
        seller_id INTEGER,
        agreed_price TEXT,
        currency_type TEXT,
        holding_amount TEXT,
        status TEXT DEFAULT 'pending',
        agreed_by TEXT DEFAULT '',
        confirm_decision TEXT DEFAULT '',
        invite_link TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    defaults = [
        ('upi_photo_id', ''),
        ('crypto_address', 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh'),
        ('crypto_network', 'Bep20'),
        ('crypto_fees', 'Fees'),
        ('vouch_link', 'https://t.me/Secureble/24?comment=1'),
        ('vouch_username', MM_USERNAME),
        ('vouch_forward_chat', str(VOUCH_FORWARD_CHANNEL_ID)),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))
    
    c.execute('''CREATE TABLE IF NOT EXISTS deal_counter (
        chat_id INTEGER PRIMARY KEY,
        counter INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS editing_sessions (
        user_id INTEGER PRIMARY KEY,
        field TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS forwarded_vouches (
        message_id INTEGER,
        chat_id INTEGER,
        forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (message_id, chat_id)
    )''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def get_config(key: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ''

def set_config(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def is_vouch_forwarded(chat_id: int, message_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT message_id FROM forwarded_vouches WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_vouch_forwarded(chat_id: int, message_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO forwarded_vouches (chat_id, message_id) VALUES (?, ?)", (chat_id, message_id))
    conn.commit()
    conn.close()

def get_next_deal_number(chat_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT counter FROM deal_counter WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    if row:
        counter = row[0] + 1
        c.execute("UPDATE deal_counter SET counter = ? WHERE chat_id = ?", (counter, chat_id))
    else:
        counter = 1
        c.execute("INSERT INTO deal_counter (chat_id, counter) VALUES (?, ?)", (chat_id, counter))
    conn.commit()
    conn.close()
    return counter

def create_deal(chat_id: int, deal_number: int, terms: str = '', buyer_id: int = 0, seller_id: int = 0, price: str = '', currency: str = '', holding: str = '') -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO deals (chat_id, deal_number, deal_terms, buyer_id, seller_id, agreed_price, currency_type, holding_amount)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (chat_id, deal_number, terms, buyer_id, seller_id, price, currency, holding))
    deal_id = c.lastrowid
    conn.commit()
    conn.close()
    return deal_id

def get_deal_by_chat(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM deals WHERE chat_id = ? ORDER BY deal_id DESC LIMIT 1", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row:
        columns = ['deal_id', 'chat_id', 'deal_number', 'deal_terms', 'buyer_id', 'seller_id', 
                   'agreed_price', 'currency_type', 'holding_amount', 'status', 'agreed_by', 
                   'confirm_decision', 'invite_link', 'created_at']
        return dict(zip(columns, row))
    return None

def get_deal_by_number(deal_number: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM deals WHERE deal_number = ? ORDER BY deal_id DESC LIMIT 1", (deal_number,))
    row = c.fetchone()
    conn.close()
    if row:
        columns = ['deal_id', 'chat_id', 'deal_number', 'deal_terms', 'buyer_id', 'seller_id', 
                   'agreed_price', 'currency_type', 'holding_amount', 'status', 'agreed_by', 
                   'confirm_decision', 'invite_link', 'created_at']
        return dict(zip(columns, row))
    return None

def update_deal_by_chat(chat_id: int, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT deal_id FROM deals WHERE chat_id = ? ORDER BY deal_id DESC LIMIT 1", (chat_id,))
    row = c.fetchone()
    if row:
        deal_id = row[0]
        sets = []
        values = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            values.append(v)
        values.append(deal_id)
        c.execute(f"UPDATE deals SET {', '.join(sets)} WHERE deal_id = ?", values)
    conn.commit()
    conn.close()

def update_deal_by_number(deal_number: int, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT deal_id FROM deals WHERE deal_number = ? ORDER BY deal_id DESC LIMIT 1", (deal_number,))
    row = c.fetchone()
    if row:
        deal_id = row[0]
        sets = []
        values = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            values.append(v)
        values.append(deal_id)
        c.execute(f"UPDATE deals SET {', '.join(sets)} WHERE deal_id = ?", values)
    conn.commit()
    conn.close()

# ─── HELPERS ─────────────────────────────────────────────────────────────────

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id in ADMIN_IDS:
        return True
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except:
        return False

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except:
        pass

def get_mention(user_id: int, name: str = "") -> str:
    return f"<a href='tg://user?id={user_id}'>{name or 'User'}</a>"

async def set_title(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str):
    try:
        await context.bot.set_chat_title(chat_id=update.effective_chat.id, title=title)
    except Exception as e:
        logger.error(f"set_title failed: {e}")

async def pin_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    try:
        await context.bot.pin_chat_message(chat_id=update.effective_chat.id, message_id=message_id)
    except Exception as e:
        logger.error(f"pin_msg failed: {e}")

async def create_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        link = await context.bot.create_chat_invite_link(
            chat_id=update.effective_chat.id,
            member_limit=2
        )
        return link.invite_link
    except Exception as e:
        logger.error(f"create_link failed: {e}")
        return ""

def parse_cmd(text: str) -> tuple:
    if not text:
        return None, None
    text = text.strip()
    prefix = text[0] if text else ''
    if prefix in ['/', '.']:
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ''
        args = parts[1] if len(parts) > 1 else ''
        return cmd, args
    return None, None

# ─── COMMAND HANDLERS ───────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != 'private':
        return
    keyboard = [[InlineKeyboardButton(f"MM", url=f"https://t.me/{MM_USERNAME}")]]
    await context.bot.send_message(
        chat_id=chat.id,
        text=f"Welcome to the MM Service of @{MM_USERNAME}.\nContact Below For Making Secure Gc.\n\nThank you, Have a Nice Day.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    logger.info(f"Start command from {update.effective_user.id}")

async def set_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    deal_number = get_next_deal_number(chat_id)
    link = await create_link(update, context)
    create_deal(chat_id, deal_number)
    await delete_cmd(update, context)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Please join & share this with the other user involved in the deal.\n\n🔗 Invite Link - {link}",
        parse_mode=ParseMode.HTML
    )
    msg2 = await context.bot.send_message(
        chat_id=chat_id,
        text="Hey. Please state the terms of the deal.\n\n• What is the deal?\n• Who is the buyer/seller?\n• What is the agreed price and which crypto or currency.\n• Include any other relevant information."
    )
    update_deal_by_chat(chat_id, invite_link=link)
    await asyncio.sleep(0.5)
    await pin_msg(update, context, msg2.message_id)
    logger.info(f"Set deal #{deal_number} in chat {chat_id}")

async def rec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_cmd(update.message.text)
        args_text = extracted_args
    
    amount = ""
    deal_number = None
    if args_text:
        numbers = re.findall(r'\d+', args_text)
        if len(numbers) >= 2:
            amount = numbers[0]
            deal_number = int(numbers[1])
        elif len(numbers) == 1:
            amount = numbers[0]
    
    if deal_number is None:
        deal = get_deal_by_chat(update.effective_chat.id)
        deal_number = deal['deal_number'] if deal else 0
    
    currency_symbol = "₹"
    if ' $' in args_text or args_text.startswith('$'):
        currency_symbol = "$"
    amount_clean = amount
    
    await delete_cmd(update, context)
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ I have successfully received the amount and the MM fee.\nIt is safe to deal forward.\n\nI will process the payment after the deal concludes.\nThank you for your cooperation and for your trust!"
    )
    
    if amount_clean and deal_number:
        update_deal_by_chat(update.effective_chat.id, holding_amount=f"{currency_symbol}{amount_clean}")
        await set_title(update, context, f"Deal #{deal_number} • @Holding {currency_symbol}{amount_clean}")
    
    await asyncio.sleep(0.5)
    await pin_msg(update, context, msg.message_id)
    logger.info(f"Rec deal #{deal_number} amount {currency_symbol}{amount_clean}")

async def agree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    if not update.message.reply_to_message:
        try:
            await update.message.reply_text("Reply to a user to set them as agreement confirmer.")
        except:
            pass
        await delete_cmd(update, context)
        return
    target_user = update.message.reply_to_message.from_user
    target_id = target_user.id
    target_name = target_user.full_name or "User"
    mention = get_mention(target_id, target_name)
    keyboard = [[InlineKeyboardButton(f"Agree - {target_name}", callback_data=f"agree_{target_id}_{update.effective_chat.id}")]]
    await delete_cmd(update, context)
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📝 Deal Agreement\n\nPlease confirm that you agree to the terms stated above.\n\n{mention} can confirm this agreement.\n\nClick the button below to confirm your agreement.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(0.5)
    await pin_msg(update, context, msg.message_id)
    logger.info(f"Agree button set for user {target_id}")

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    if not update.message.reply_to_message:
        try:
            await update.message.reply_text("Reply to a user to set them as decision maker.")
        except:
            pass
        await delete_cmd(update, context)
        return
    target_user = update.message.reply_to_message.from_user
    target_id = target_user.id
    target_name = target_user.full_name or "User"
    mention = get_mention(target_id, target_name)
    keyboard = [
        [
            InlineKeyboardButton(f"Release - {target_name}", callback_data=f"release_{target_id}_{update.effective_chat.id}"),
            InlineKeyboardButton(f"Refund - {target_name}", callback_data=f"refund_{target_id}_{update.effective_chat.id}")
        ]
    ]
    await delete_cmd(update, context)
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔄 Final Confirmation\n\nWhen deal is Done. Please choose an action:\n\nOnly {mention} can make this decision.\n\nRelease - Funds will be released to the seller\nRefund - Funds will be refunded to the buyer",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(0.5)
    await pin_msg(update, context, msg.message_id)
    logger.info(f"Confirm button set for user {target_id}")

async def inr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_cmd(update.message.text)
        args_text = extracted_args
    amount = args_text or "0"
    photo_id = get_config('upi_photo_id')
    text = f"Pay on this Qr, Must Send the payment Screenshot.\n\n💰 Deal Amount + {amount} Fees"
    await delete_cmd(update, context)
    if photo_id:
        try:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption=text)
        except:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{text}\n\n(UPI QR photo not available, please use setinrphoto in bot DM)")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{text}\n\n(UPI QR not set. Admin please use setinrphoto in bot DM)")

async def crp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_cmd(update.message.text)
        args_text = extracted_args
    amount = args_text or "0"
    address = get_config('crypto_address')
    network = get_config('crypto_network')
    fees = get_config('crypto_fees')
    text = f"Network: {network}\nAddress: {address}\n\n💰 Deal Amount + {fees} {amount}\n\n⚠️ Please double-check the network before sending."
    await delete_cmd(update, context)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

async def link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    l = await create_link(update, context)
    await delete_cmd(update, context)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Please join & share this with the other user involved in the deal.\n\n🔗 Invite Link - {l}", parse_mode=ParseMode.HTML)

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_cmd(update.message.text)
        args_text = extracted_args
    deal_number = None
    if args_text and args_text.strip().isdigit():
        deal_number = int(args_text.strip())
    if deal_number is None:
        deal = get_deal_by_chat(update.effective_chat.id)
        deal_number = deal['deal_number'] if deal else 0
    await delete_cmd(update, context)
    await set_title(update, context, f"Deal #{deal_number} • @Completed")
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Thank you for using my Middleman service! 🤝\n\nPlease leave me a vouch here\n\n<code>Vouch @{MM_USERNAME} MMD</code>",
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(0.5)
    await pin_msg(update, context, msg.message_id)
    logger.info(f"Done deal #{deal_number}")

async def lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    await delete_cmd(update, context)
    try:
        permissions = ChatPermissions.no_permissions()
        await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
        await context.bot.send_message(chat_id=chat_id, text="🔒 Group has been now locked. Have a nice day, bye.")
    except Exception as e:
        logger.error(f"Lock failed: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Lock failed. Bot needs 'restrict members' permission.")

async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    await delete_cmd(update, context)
    try:
        permissions = ChatPermissions.all_permissions()
        await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
        await context.bot.send_message(chat_id=chat_id, text="🔓 Group has been now unlocked, now you can message here.")
    except Exception as e:
        logger.error(f"Unlock failed: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Unlock failed.")

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    if not update.message.reply_to_message:
        await delete_cmd(update, context)
        return
    target = update.message.reply_to_message.from_user
    mention = get_mention(target.id, target.full_name or "User")
    await delete_cmd(update, context)
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target.id)
        await context.bot.send_message(chat_id=chat_id, text=f"{mention} banned 🚫", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ban failed: {e}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    if not update.message.reply_to_message:
        await delete_cmd(update, context)
        return
    target = update.message.reply_to_message.from_user
    mention = get_mention(target.id, target.full_name or "User")
    await delete_cmd(update, context)
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=target.id)
        await context.bot.send_message(chat_id=chat_id, text=f"{mention} unbanned ✅", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Unban failed: {e}")

async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    if not update.message.reply_to_message:
        await delete_cmd(update, context)
        return
    target = update.message.reply_to_message.from_user
    mention = get_mention(target.id, target.full_name or "User")
    await delete_cmd(update, context)
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target.id)
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=target.id)
        await context.bot.send_message(chat_id=chat_id, text=f"{mention} kicked 🦵", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Kick failed: {e}")

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    if not update.message.reply_to_message:
        await delete_cmd(update, context)
        return
    target = update.message.reply_to_message.from_user
    mention = get_mention(target.id, target.full_name or "User")
    await delete_cmd(update, context)
    try:
        permissions = ChatPermissions(
            can_send_messages=False, can_send_audios=False, can_send_documents=False,
            can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
            can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
            can_add_web_page_previews=False, can_change_info=False, can_invite_users=False,
            can_pin_messages=False, can_manage_topics=False
        )
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=target.id, permissions=permissions)
        await context.bot.send_message(chat_id=chat_id, text=f"{mention} muted 🔇", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Mute failed: {e}")

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    chat_id = update.effective_chat.id
    if not update.message.reply_to_message:
        await delete_cmd(update, context)
        return
    target = update.message.reply_to_message.from_user
    mention = get_mention(target.id, target.full_name or "User")
    await delete_cmd(update, context)
    try:
        permissions = ChatPermissions.all_permissions()
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=target.id, permissions=permissions)
        await context.bot.send_message(chat_id=chat_id, text=f"{mention} unmuted 🔊", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Unmute failed: {e}")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    await delete_cmd(update, context)
    text = f"👤 **Your Info**\nUser ID: `{user.id}`\nUsername: @{user.username or 'N/A'}\nFull Name: {user.full_name}\n\n💬 **Chat Info**\nChat ID: `{chat.id}`\nChat Type: {chat.type}\nChat Title: {chat.title or 'N/A'}\n\n📢 **Vouch Forward Channel**\nChannel ID: `{VOUCH_FORWARD_CHANNEL_ID}`"
    await context.bot.send_message(chat_id=chat.id, text=text, parse_mode=ParseMode.MARKDOWN)

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """📋 **Available Commands**\n\n**Deal Management:**\nset - Clear msgs + send invite link + deal msg + pin\nrec [amount] [deal_number] - Confirm payment received & pin\nagree - Reply to a user to set them as agreement confirmer\nconfirm - Reply to a user to give release/refund decision\ninr [amount] - Send UPI payment details with amount\ncrp [amount] - Send crypto payment details with amount\nlink - Create invite link only\ndone [deal_number] - Mark deal completed + set group name\n\n**Group Control:**\nlock - Lock group (read-only)\nunlock - Unlock group\n\n**User Management (reply to their message):**\nkick - Kick user from group\nban - Ban user from group\nunban - Unban user\nmute - Mute user\nunmute - Unmute user\n\n**Admin DM Only (edit config via bot):**\nsetinrphoto - Change UPI photo\neditcrp - Edit crypto address & fees\nsetvouch - Edit vouch username & link\ncancel - Cancel any editing session\n\n**Utility:**\nid - Show IDs\nclose - Close deal with 30min auto-delete\nhelp - This message\n\n💡 Commands work with or without / or . prefix!"""
    await delete_cmd(update, context)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=ParseMode.MARKDOWN)

async def close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_cmd(update, context)
        return
    deal = get_deal_by_chat(update.effective_chat.id)
    status = deal['status'] if deal else 'completed'
    await delete_cmd(update, context)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🤝 This deal is now closed with status: {status}.\n\nThe group will be deleted automatically in 30 minutes.")
    await asyncio.sleep(1800)
    try:
        await context.bot.leave_chat(update.effective_chat.id)
    except:
        pass

# ─── ADMIN DM COMMANDS ───────────────────────────────────────────────────────

async def setinrphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != 'private':
        await update.message.reply_text("This command works only in bot DM.")
        return
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("You are not authorized.")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO editing_sessions (user_id, field) VALUES (?, ?)", (user.id, 'upi_photo'))
    conn.commit()
    conn.close()
    await update.message.reply_text("Send me the new UPI QR photo. Send cancel to cancel.")

async def editcrp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != 'private':
        await update.message.reply_text("This command works only in bot DM.")
        return
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("You are not authorized.")
        return
    address = get_config('crypto_address')
    network = get_config('crypto_network')
    fees = get_config('crypto_fees')
    await update.message.reply_text(f"**Current Config:**\nNetwork: {network}\nAddress: {address}\nFees: {fees}\n\nSend the new address to update.\nUse format: `ADDRESS|NETWORK|FEES`\nExample: `0xabc...|Bep20|1`\n\nOr send cancel to cancel.", parse_mode=ParseMode.MARKDOWN)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO editing_sessions (user_id, field) VALUES (?, ?)", (user.id, 'crypto_config'))
    conn.commit()
    conn.close()

async def setvouch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != 'private':
        await update.message.reply_text("This command works only in bot DM.")
        return
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("You are not authorized.")
        return
    current_link = get_config('vouch_link')
    current_user = get_config('vouch_username')
    await update.message.reply_text(f"**Current Vouch Config:**\nUsername: @{current_user}\nLink: {current_link}\n\nSend the new values in format:\n`USERNAME|LINK`\n\nExample: `shuify|https://t.me/Secureble/24?comment=1`\n\nOr send cancel to cancel.", parse_mode=ParseMode.MARKDOWN)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO editing_sessions (user_id, field) VALUES (?, ?)", (user.id, 'vouch_config'))
    conn.commit()
    conn.close()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != 'private':
        await update.message.reply_text("This works only in bot DM.")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM editing_sessions WHERE user_id = ?", (user.id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("Editing session cancelled.")

# ─── CALLBACK QUERY HANDLERS ─────────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    await query.answer()
    
    if data.startswith("agree_"):
        parts = data.split("_")
        target_id = int(parts[1])
        chat_id = int(parts[2])
        if user.id != target_id:
            await query.answer("You are not authorized to confirm this agreement!", show_alert=True)
            return
        mention = get_mention(user.id, user.full_name)
        new_text = f"Agreed by the dealer. 🤝\n\nBoth users have agreed to the deal terms.\nConfirmed by: {mention}\n\nNow, continue the deal."
        await query.edit_message_text(text=new_text, parse_mode=ParseMode.HTML)
        update_deal_by_chat(chat_id, agreed_by=str(user.id))
        logger.info(f"User {user.id} agreed to deal in chat {chat_id}")
    
    elif data.startswith("release_"):
        parts = data.split("_")
        target_id = int(parts[1])
        chat_id = int(parts[2])
        if user.id != target_id:
            await query.answer("You are not authorized to make this decision!", show_alert=True)
            return
        mention = get_mention(user.id, user.full_name)
        new_text = f"Funds Released initiated 🎉\n\nThe buyer has agreed to release the funds.\nAction taken by: {mention}"
        await query.edit_message_text(text=new_text, parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=chat_id, text=f"Released confirmation occurs 🚀\n\n@{MM_USERNAME} please release the funds.\n\nSeller, Please Drop the Qr or upi!\n\nNow, Wait @{MM_USERNAME} Response as soon as possible.")
        update_deal_by_chat(chat_id, confirm_decision='release', status='completed')
        logger.info(f"Release by {user.id} in chat {chat_id}")
    
    elif data.startswith("refund_"):
        parts = data.split("_")
        target_id = int(parts[1])
        chat_id = int(parts[2])
        if user.id != target_id:
            await query.answer("You are not authorized to make this decision!", show_alert=True)
            return
        mention = get_mention(user.id, user.full_name)
        new_text = f"Refund Initiated ↩️\n\nThe buyer has agreed to refund the amount.\nAction taken by: {mention}"
        await query.edit_message_text(text=new_text, parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=chat_id, text=f"Refund confirmation occurs ↩️\n\n@{MM_USERNAME} please process the refund.\n\nRefund has been requested!\nSeller Please Confirm this Refund and Buyer Drop the Qr or Upi.\n\nNow, Wait @{MM_USERNAME} Response as soon as possible.")
        update_deal_by_chat(chat_id, confirm_decision='refund', status='refunded')
        logger.info(f"Refund by {user.id} in chat {chat_id}")

# ─── MESSAGE HANDLERS ────────────────────────────────────────────────────────

async def handle_dm_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != 'private':
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT field FROM editing_sessions WHERE user_id = ?", (user.id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return
    
    field = row[0]
    
    if field == 'upi_photo':
        if update.message.photo:
            photo_id = update.message.photo[-1].file_id
            set_config('upi_photo_id', photo_id)
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM editing_sessions WHERE user_id = ?", (user.id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("UPI photo updated successfully!")
            logger.info(f"UPI photo updated by {user.id}")
        else:
            await update.message.reply_text("Please send a photo. Send cancel to cancel.")
    
    elif field == 'crypto_config':
        text = update.message.text
        if not text:
            await update.message.reply_text("Please send the address in format: `ADDRESS|NETWORK|FEES`", parse_mode=ParseMode.MARKDOWN)
            return
        parts = text.split("|")
        if len(parts) >= 1:
            set_config('crypto_address', parts[0].strip())
        if len(parts) >= 2:
            set_config('crypto_network', parts[1].strip())
        if len(parts) >= 3:
            set_config('crypto_fees', parts[2].strip())
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM editing_sessions WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"Crypto config updated!\n\nNetwork: {get_config('crypto_network')}\nAddress: {get_config('crypto_address')}\nFees: {get_config('crypto_fees')}")
        logger.info(f"Crypto config updated by {user.id}")
    
    elif field == 'vouch_config':
        text = update.message.text
        if not text:
            await update.message.reply_text("Please send in format: `USERNAME|LINK`", parse_mode=ParseMode.MARKDOWN)
            return
        parts = text.split("|")
        if len(parts) >= 1:
            set_config('vouch_username', parts[0].strip())
        if len(parts) >= 2:
            set_config('vouch_link', parts[1].strip())
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM editing_sessions WHERE user_id = ?", (user.id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"Vouch config updated!\n\nUsername: @{get_config('vouch_username')}\nLink: {get_config('vouch_link')}")
        logger.info(f"Vouch config updated by {user.id}")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        return
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # ─── VOUCH DETECTION ─────────────────────────────────────────────────
    vouch_pattern = re.compile(r'vouch\s+@(\w+)\s+mmd', re.IGNORECASE)
    match = vouch_pattern.search(text)
    if match:
        vouch_username = match.group(1)
        chat_id = update.effective_chat.id
        message_id = update.message.message_id
        if not is_vouch_forwarded(chat_id, message_id):
            try:
                await context.bot.forward_message(chat_id=VOUCH_FORWARD_CHANNEL_ID, from_chat_id=chat_id, message_id=message_id)
                mark_vouch_forwarded(chat_id, message_id)
                logger.info(f" Vouch from @{vouch_username} forwarded to {VOUCH_FORWARD_CHANNEL_ID}")
            except Exception as e:
                logger.error(f" Vouch forward failed: {e}")
    
    # ─── COMMAND DETECTION WITHOUT PREFIX ────────────────────────────────
    if text.startswith('/') or text.startswith('.'):
        return
    
    before_cmd = text.split(None, 1)[0].lower()
    known_commands = [
        'set', 'rec', 'agree', 'confirm', 'inr', 'crp', 'link', 'done',
        'lock', 'unlock', 'kick', 'ban', 'unban', 'mute', 'unmute',
        'id', 'help', 'close'
    ]
    
    if before_cmd not in known_commands:
        return
    
    if ' ' in text:
        args_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ''
        context.args = args_text.split()
    else:
        context.args = []
    
    handler_map = {
        'set': set_deal, 'rec': rec, 'agree': agree, 'confirm': confirm,
        'inr': inr, 'crp': crp, 'link': link, 'done': done,
        'lock': lock, 'unlock': unlock, 'kick': kick, 'ban': ban,
        'unban': unban, 'mute': mute, 'unmute': unmute,
        'id': cmd_id, 'help': help, 'close': close,
    }
    
    handler = handler_map.get(before_cmd)
    if handler:
        try:
            await handler(update, context)
        except Exception as e:
            logger.error(f"Handler {before_cmd} error: {e}")

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    if result.new_chat_member.status == ChatMember.MEMBER:
        logger.info(f"Bot added to: {chat.title} ({chat.id})")
    elif result.new_chat_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
        logger.info(f"Bot removed from: {chat.title} ({chat.id})")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    
    # Start fake HTTP server for Render
    http_thread = threading.Thread(target=run_fake_server, daemon=True)
    http_thread.start()
    
    # Build application
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )
    
    # Register all handlers
    app.add_handler(CommandHandler("set", set_deal))
    app.add_handler(CommandHandler("rec", rec))
    app.add_handler(CommandHandler("agree", agree))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("inr", inr))
    app.add_handler(CommandHandler("crp", crp))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("lock", lock))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("close", close))
    app.add_handler(CommandHandler("setinrphoto", setinrphoto))
    app.add_handler(CommandHandler("editcrp", editcrp))
    app.add_handler(CommandHandler("setvouch", setvouch))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_dm_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, handle_group_message))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(error_handler)
    
    logger.info(" Bot starting with fake HTTP server...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
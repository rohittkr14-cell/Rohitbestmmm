import os
import sqlite3
import re
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, ChatMember
from telegram._update import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes
from telegram.constants import ParseMode

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
BOT_TOKEN = "8671613935:AAFsG7gbKFjZ2VRdKQaJZnGTrut__K9M59w"
BOT_USERNAME = "Secureblebot"
ADMIN_IDS = [7691071175]
MM_USERNAME = "shuify"

# ─── YAHAN SIRF CHANNEL ID DAALO ──────────────────────────────────────────
# Bot ko is channel mein ADMIN hona chahiye
# Vouch messages yahan forward honge
VOUCH_FORWARD_CHANNEL_ID = -1003711319131  # ⚠️ YE CHANNEL KI ID DAALO, GROUP KI NAHI
# CHANNEL ID nikalne ke liye:
# 1. Bot ko channel mein admin banao
# 2. Channel mein koi bhi message forward karo bot DM mein
# 3. Bot DM mein /id likho — wahan channel ID dikhega

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

def update_deal(chat_id: int, **kwargs):
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

async def delete_command_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except:
        pass

def get_mention(user_id: int, name: str = "") -> str:
    return f"<a href='tg://user?id={user_id}'>{name or 'User'}</a>"

async def set_group_title(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str):
    try:
        await context.bot.set_chat_title(chat_id=update.effective_chat.id, title=title)
    except Exception as e:
        logger.error(f"Failed to set title: {e}")

async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    try:
        await context.bot.pin_chat_message(chat_id=update.effective_chat.id, message_id=message_id)
    except Exception as e:
        logger.error(f"Failed to pin: {e}")

async def create_expiring_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        link = await context.bot.create_chat_invite_link(
            chat_id=update.effective_chat.id,
            member_limit=2
        )
        return link.invite_link
    except Exception as e:
        logger.error(f"Failed to create link: {e}")
        return ""

def parse_command(text: str) -> tuple:
    if not text:
        return None, None
    
    text = text.strip()
    
    if text.startswith('/') or text.startswith('.'):
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ''
        args = parts[1] if len(parts) > 1 else ''
        return cmd, args
    else:
        known_commands = [
            'set', 'rec', 'agree', 'confirm', 'inr', 'crp', 'link', 'done',
            'lock', 'unlock', 'kick', 'ban', 'unban', 'mute', 'unmute',
            'id', 'help', 'close',
            'setinrphoto', 'editcrp', 'cancel', 'setvouch', 'start'
        ]
        first_word = text.split(None, 1)[0].lower()
        if first_word in known_commands:
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ''
            return cmd, args
        
        return None, None

def detect_amount_currency(text: str) -> tuple:
    if not text:
        return "", ""
    
    text = text.strip()
    currency_symbol = "₹"
    
    if text.startswith('$') or ' $' in text:
        currency_symbol = "$"
    elif text.startswith('₹') or ' ₹' in text:
        currency_symbol = "₹"
    
    amount_clean = text.replace('₹', '').replace('$', '').strip()
    
    return amount_clean, currency_symbol

# ─── COMMAND HANDLERS ───────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    
    if chat.type != 'private':
        return
    
    keyboard = [[InlineKeyboardButton(f"MM", url=f"https://t.me/{MM_USERNAME}")]]
    
    await context.bot.send_message(
        chat_id=chat.id,
        text=f"Welcome to the MM Service of @{MM_USERNAME}.\nContact Below For Making Secure Gc.\n\nThank you, Have a Nice Day.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    deal_number = get_next_deal_number(chat_id)
    
    link = await create_expiring_link(update, context)
    create_deal(chat_id, deal_number)
    
    await delete_command_msg(update, context)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Please join & share this with the other user involved in the deal.\n\n🔗 Invite Link - {link}",
        parse_mode=ParseMode.HTML
    )
    
    msg2 = await context.bot.send_message(
        chat_id=chat_id,
        text="Hey. Please state the terms of the deal.\n\n• What is the deal?\n• Who is the buyer/seller?\n• What is the agreed price and which crypto or currency.\n• Include any other relevant information."
    )
    
    update_deal(chat_id, invite_link=link)
    
    await asyncio.sleep(0.5)
    await pin_message(update, context, msg2.message_id)

async def cmd_rec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_command(update.message.text)
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
    
    amount_clean, currency_symbol = detect_amount_currency(amount)
    if not amount_clean:
        amount_clean = amount
    
    await delete_command_msg(update, context)
    
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ I have successfully received the amount and the MM fee.\nIt is safe to deal forward.\n\nI will process the payment after the deal concludes.\nThank you for your cooperation and for your trust!"
    )
    
    if amount_clean and deal_number:
        update_deal(update.effective_chat.id, holding_amount=f"{currency_symbol}{amount_clean}")
        await set_group_title(update, context, f"Deal #{deal_number} • @Holding {currency_symbol}{amount_clean}")
    
    await asyncio.sleep(0.5)
    await pin_message(update, context, msg.message_id)

async def cmd_agree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    if not update.message.reply_to_message:
        try:
            await update.message.reply_text("Reply to a user to set them as agreement confirmer.")
        except:
            pass
        await delete_command_msg(update, context)
        return
    
    target_user = update.message.reply_to_message.from_user
    target_id = target_user.id
    target_name = target_user.full_name or "User"
    
    mention = get_mention(target_id, target_name)
    
    keyboard = [[InlineKeyboardButton(f"Agree - {target_name}", callback_data=f"agree_{target_id}_{update.effective_chat.id}")]]
    
    await delete_command_msg(update, context)
    
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📝 Deal Agreement\n\nPlease confirm that you agree to the terms stated above.\n\n{mention} can confirm this agreement.\n\nClick the button below to confirm your agreement.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    await asyncio.sleep(0.5)
    await pin_message(update, context, msg.message_id)

async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    if not update.message.reply_to_message:
        try:
            await update.message.reply_text("Reply to a user to set them as decision maker.")
        except:
            pass
        await delete_command_msg(update, context)
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
    
    await delete_command_msg(update, context)
    
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔄 Final Confirmation\n\nWhen deal is Done. Please choose an action:\n\nOnly {mention} can make this decision.\n\nRelease - Funds will be released to the seller\nRefund - Funds will be refunded to the buyer",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    
    await asyncio.sleep(0.5)
    await pin_message(update, context, msg.message_id)

async def cmd_inr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_command(update.message.text)
        args_text = extracted_args
    
    amount = args_text or "0"
    photo_id = get_config('upi_photo_id')
    
    text = f"Pay on this Qr, Must Send the payment Screenshot.\n\n💰 Deal Amount + {amount} Fees"
    
    await delete_command_msg(update, context)
    
    if photo_id:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo_id,
                caption=text
            )
        except:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"{text}\n\n(UPI QR photo not available, please use setinrphoto in bot DM)"
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{text}\n\n(UPI QR not set. Admin please use setinrphoto in bot DM)"
        )

async def cmd_crp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_command(update.message.text)
        args_text = extracted_args
    
    amount = args_text or "0"
    address = get_config('crypto_address')
    network = get_config('crypto_network')
    fees = get_config('crypto_fees')
    
    text = f"""Network: {network}
Address: {address}

💰 Deal Amount + {fees} {amount}

⚠️ Please double-check the network before sending."""
    
    await delete_command_msg(update, context)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text
    )

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    link = await create_expiring_link(update, context)
    
    await delete_command_msg(update, context)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Please join & share this with the other user involved in the deal.\n\n🔗 Invite Link - {link}",
        parse_mode=ParseMode.HTML
    )

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    args_text = " ".join(context.args) if context.args else ""
    if not args_text and hasattr(update.message, 'text'):
        cmd, extracted_args = parse_command(update.message.text)
        args_text = extracted_args
    
    deal_number = None
    
    if args_text and args_text.strip().isdigit():
        deal_number = int(args_text.strip())
    
    if deal_number is None:
        deal = get_deal_by_chat(update.effective_chat.id)
        deal_number = deal['deal_number'] if deal else 0
    
    await delete_command_msg(update, context)
    
    await set_group_title(update, context, f"Deal #{deal_number} • @Completed")
    
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"""Thank you for using my Middleman service! 🤝

Please leave me a vouch here

<code>Vouch @{MM_USERNAME} MMD</code>""",
        parse_mode=ParseMode.HTML
    )
    
    await asyncio.sleep(0.5)
    await pin_message(update, context, msg.message_id)

# ─── LOCK ────────────────────────────────────────────────────────
async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    await delete_command_msg(update, context)
    
    try:
        permissions = ChatPermissions.no_permissions()
        await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔒 Group has been now locked. Have a nice day, bye."
        )
        logger.info(f"Group {chat_id} locked successfully")
    except Exception as e:
        logger.error(f"Lock failed for {chat_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Lock failed. Make sure bot is admin with 'restrict members' permission. Error: {e}"
        )

# ─── UNLOCK ──────────────────────────────────────────────────────
async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    await delete_command_msg(update, context)
    
    try:
        permissions = ChatPermissions.all_permissions()
        await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔓 Group has been now unlocked, now you can message here."
        )
        logger.info(f"Group {chat_id} unlocked successfully")
    except Exception as e:
        logger.error(f"Unlock failed for {chat_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Unlock failed. Make sure bot is admin with 'restrict members' permission. Error: {e}"
        )

# ─── BAN ─────────────────────────────────────────────────────────
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    if not update.message.reply_to_message:
        await delete_command_msg(update, context)
        return
    
    target = update.message.reply_to_message.from_user
    target_name = target.full_name or "User"
    mention = get_mention(target.id, target_name)
    
    await delete_command_msg(update, context)
    
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target.id)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{mention} banned 🚫",
            parse_mode=ParseMode.HTML
        )
        logger.info(f"User {target.id} banned from {chat_id}")
    except Exception as e:
        logger.error(f"Ban failed for {target.id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ban failed. Make sure bot is admin. Error: {e}"
        )

# ─── UNBAN ───────────────────────────────────────────────────────
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    if not update.message.reply_to_message:
        await delete_command_msg(update, context)
        return
    
    target = update.message.reply_to_message.from_user
    target_name = target.full_name or "User"
    mention = get_mention(target.id, target_name)
    
    await delete_command_msg(update, context)
    
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=target.id)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{mention} unbanned ✅",
            parse_mode=ParseMode.HTML
        )
        logger.info(f"User {target.id} unbanned from {chat_id}")
    except Exception as e:
        logger.error(f"Unban failed for {target.id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Unban failed. Error: {e}"
        )

# ─── KICK ────────────────────────────────────────────────────────
async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    if not update.message.reply_to_message:
        await delete_command_msg(update, context)
        return
    
    target = update.message.reply_to_message.from_user
    target_name = target.full_name or "User"
    mention = get_mention(target.id, target_name)
    
    await delete_command_msg(update, context)
    
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target.id)
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=target.id)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{mention} kicked 🦵",
            parse_mode=ParseMode.HTML
        )
        logger.info(f"User {target.id} kicked from {chat_id}")
    except Exception as e:
        logger.error(f"Kick failed for {target.id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Kick failed. Make sure bot is admin. Error: {e}"
        )

# ─── MUTE ────────────────────────────────────────────────────────
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    if not update.message.reply_to_message:
        await delete_command_msg(update, context)
        return
    
    target = update.message.reply_to_message.from_user
    target_name = target.full_name or "User"
    mention = get_mention(target.id, target_name)
    
    await delete_command_msg(update, context)
    
    try:
        permissions = ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False
        )
        
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=target.id, permissions=permissions)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{mention} muted 🔇",
            parse_mode=ParseMode.HTML
        )
        logger.info(f"User {target.id} muted in {chat_id}")
    except Exception as e:
        logger.error(f"Mute failed for {target.id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Mute failed. Make sure bot is admin. Error: {e}"
        )

# ─── UNMUTE ──────────────────────────────────────────────────────
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    chat_id = update.effective_chat.id
    
    if not update.message.reply_to_message:
        await delete_command_msg(update, context)
        return
    
    target = update.message.reply_to_message.from_user
    target_name = target.full_name or "User"
    mention = get_mention(target.id, target_name)
    
    await delete_command_msg(update, context)
    
    try:
        permissions = ChatPermissions.all_permissions()
        
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=target.id, permissions=permissions)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{mention} unmuted 🔊",
            parse_mode=ParseMode.HTML
        )
        logger.info(f"User {target.id} unmuted in {chat_id}")
    except Exception as e:
        logger.error(f"Unmute failed for {target.id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Unmute failed. Make sure bot is admin. Error: {e}"
        )

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    await delete_command_msg(update, context)
    
    text = f"""👤 **Your Info**
User ID: `{user.id}`
Username: @{user.username or 'N/A'}
Full Name: {user.full_name}

💬 **Chat Info**
Chat ID: `{chat.id}`
Chat Type: {chat.type}
Chat Title: {chat.title or 'N/A'}

📢 **Forward Channel Info**
Channel ID (for vouch forwarding): `{VOUCH_FORWARD_CHANNEL_ID}`
Make sure bot is admin in that channel!"""
    
    await context.bot.send_message(chat_id=chat.id, text=text, parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """📋 **Available Commands**

**Deal Management:**
set - Clear msgs + send invite link + deal msg + pin
rec [amount] [deal_number] - Confirm payment received & pin
agree - Reply to a user to set them as agreement confirmer
confirm - Reply to a user to give release/refund decision
inr [amount] - Send UPI payment details with amount
crp [amount] - Send crypto payment details with amount
link - Create invite link only
done [deal_number] - Mark deal completed + set group name

**Group Control:**
lock - Lock group (read-only)
unlock - Unlock group

**User Management (reply to their message):**
kick - Kick user from group
ban - Ban user from group
unban - Unban user
mute - Mute user
unmute - Unmute user

**Admin DM Only (edit config via bot):**
setinrphoto - Change UPI photo
editcrp - Edit crypto address & fees
setvouch - Edit vouch username & link
cancel - Cancel any editing session

**Utility:**
id - Show IDs
close - Close deal with 30min auto-delete
help - This message

💡 Commands work with or without / or . prefix!"""
    
    await delete_command_msg(update, context)
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=ParseMode.MARKDOWN)

async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await delete_command_msg(update, context)
        return
    
    deal = get_deal_by_chat(update.effective_chat.id)
    status = deal['status'] if deal else 'completed'
    
    await delete_command_msg(update, context)
    
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🤝 This deal is now closed with status: {status}.\n\nThe group will be deleted automatically in 30 minutes."
    )
    
    # 30 min baad bot group leave karega
    await asyncio.sleep(1800)
    try:
        await context.bot.leave_chat(update.effective_chat.id)
    except:
        pass

# ─── ADMIN DM COMMANDS ───────────────────────────────────────────────────────

async def cmd_setinrphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def cmd_editcrp(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(
        f"**Current Config:**\n"
        f"Network: {network}\n"
        f"Address: {address}\n"
        f"Fees: {fees}\n\n"
        f"Send the new address to update.\n"
        f"Use format: `ADDRESS|NETWORK|FEES`\n"
        f"Example: `0xabc...|Bep20|1`\n\n"
        f"Or send cancel to cancel."
    )
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO editing_sessions (user_id, field) VALUES (?, ?)", (user.id, 'crypto_config'))
    conn.commit()
    conn.close()

async def cmd_setvouch(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(
        f"**Current Vouch Config:**\n"
        f"Username: @{current_user}\n"
        f"Link: {current_link}\n\n"
        f"Send the new values in format:\n"
        f"`USERNAME|LINK`\n\n"
        f"Example: `shuify|https://t.me/Secureble/24?comment=1`\n\n"
        f"Or send cancel to cancel.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO editing_sessions (user_id, field) VALUES (?, ?)", (user.id, 'vouch_config'))
    conn.commit()
    conn.close()

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        await query.edit_message_text(
            text=new_text,
            parse_mode=ParseMode.HTML
        )
        
        update_deal(chat_id, agreed_by=str(user.id))
    
    elif data.startswith("release_"):
        parts = data.split("_")
        target_id = int(parts[1])
        chat_id = int(parts[2])
        
        if user.id != target_id:
            await query.answer("You are not authorized to make this decision!", show_alert=True)
            return
        
        mention = get_mention(user.id, user.full_name)
        
        new_text = f"Funds Released initiated 🎉\n\nThe buyer has agreed to release the funds.\nAction taken by: {mention}"
        
        await query.edit_message_text(
            text=new_text,
            parse_mode=ParseMode.HTML
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Released confirmation occurs 🚀\n\n@{MM_USERNAME} please release the funds.\n\nSeller, Please Drop the Qr or upi!\n\nNow, Wait @{MM_USERNAME} Response as soon as possible."
        )
        
        update_deal(chat_id, confirm_decision='release', status='completed')
    
    elif data.startswith("refund_"):
        parts = data.split("_")
        target_id = int(parts[1])
        chat_id = int(parts[2])
        
        if user.id != target_id:
            await query.answer("You are not authorized to make this decision!", show_alert=True)
            return
        
        mention = get_mention(user.id, user.full_name)
        
        new_text = f"Refund Initiated ↩️\n\nThe buyer has agreed to refund the amount.\nAction taken by: {mention}"
        
        await query.edit_message_text(
            text=new_text,
            parse_mode=ParseMode.HTML
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Refund confirmation occurs ↩️\n\n@{MM_USERNAME} please process the refund.\n\nRefund has been requested!\nSeller Please Confirm this Refund and Buyer Drop the Qr or Upi.\n\nNow, Wait @{MM_USERNAME} Response as soon as possible."
        )
        
        update_deal(chat_id, confirm_decision='refund', status='refunded')

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
        else:
            await update.message.reply_text("Please send a photo. Send cancel to cancel.")
    
    elif field == 'crypto_config':
        text = update.message.text
        
        if not text:
            await update.message.reply_text("Please send the address in format: `ADDRESS|NETWORK|FEES`")
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
        
        await update.message.reply_text(
            f"Crypto config updated!\n\n"
            f"Network: {get_config('crypto_network')}\n"
            f"Address: {get_config('crypto_address')}\n"
            f"Fees: {get_config('crypto_fees')}"
        )
    
    elif field == 'vouch_config':
        text = update.message.text
        
        if not text:
            await update.message.reply_text("Please send in format: `USERNAME|LINK`")
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
        
        await update.message.reply_text(
            f"Vouch config updated!\n\n"
            f"Username: @{get_config('vouch_username')}\n"
            f"Link: {get_config('vouch_link')}"
        )

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    
    if chat.type not in ['group', 'supergroup']:
        return
    
    if not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # ─── VOUCH DETECTION & FORWARD TO CHANNEL ──────────────────────────────
    # Group mein "vouch @shuify mmd" detect karega
    # Aur CHANNEL mein forward karega (group nahi)
    vouch_pattern = re.compile(r'vouch\s+@(\w+)\s+mmd', re.IGNORECASE)
    match = vouch_pattern.search(text)
    
    if match:
        vouch_username = match.group(1)
        chat_id = update.effective_chat.id   # Group jahan se forward ho raha
        message_id = update.message.message_id
        
        if not is_vouch_forwarded(chat_id, message_id):
            # ⬇️ FORWARD CHANNEL PE HO RAHA HAI ⬇️
            try:
                await context.bot.forward_message(
                    chat_id=VOUCH_FORWARD_CHANNEL_ID,  # 🔴 CHANNEL ID
                    from_chat_id=chat_id,               # Group se
                    message_id=message_id
                )
                mark_vouch_forwarded(chat_id, message_id)
                logger.info(f"✅ Vouch from @{vouch_username} forwarded to channel {VOUCH_FORWARD_CHANNEL_ID}")
            except Exception as e:
                logger.error(f"❌ Vouch forward failed: {e}")
                logger.error(f"⚠️ Make sure bot is ADMIN in channel: {VOUCH_FORWARD_CHANNEL_ID}")
                # Bot DM mein admin ko error bhej sakte hain
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=f"❌ Vouch forward failed!\nGroup: {chat_id}\nChannel: {VOUCH_FORWARD_CHANNEL_ID}\nError: {e}\n\nMake sure bot is admin in the channel!"
                        )
                    except:
                        pass
    
    # ─── PREFIX-LESS COMMANDS ────────────────────────────────────────────────
    if update.message.text.startswith('/') or update.message.text.startswith('.'):
        return
    
    cmd, args = parse_command(update.message.text)
    
    if cmd is None:
        return
    
    if args:
        context.args = args.split()
    else:
        context.args = []
    
    handler_map = {
        'set': cmd_set,
        'rec': cmd_rec,
        'agree': cmd_agree,
        'confirm': cmd_confirm,
        'inr': cmd_inr,
        'crp': cmd_crp,
        'link': cmd_link,
        'done': cmd_done,
        'lock': cmd_lock,
        'unlock': cmd_unlock,
        'kick': cmd_kick,
        'ban': cmd_ban,
        'unban': cmd_unban,
        'mute': cmd_mute,
        'unmute': cmd_unmute,
        'id': cmd_id,
        'help': cmd_help,
        'close': cmd_close,
        'start': cmd_start,
        'setinrphoto': cmd_setinrphoto,
        'editcrp': cmd_editcrp,
        'setvouch': cmd_setvouch,
        'cancel': cmd_cancel,
    }
    
    handler = handler_map.get(cmd)
    if handler:
        await handler(update, context)

# ─── CHAT MEMBER HANDLER ────────────────────────────────────────────────────

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    
    if result.new_chat_member.status == ChatMember.MEMBER:
        logger.info(f"Bot added to group: {chat.title} ({chat.id})")
    elif result.new_chat_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
        logger.info(f"Bot removed from group: {chat.title} ({chat.id})")

# ─── ERROR HANDLER ───────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Deal Management
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler("rec", cmd_rec))
    app.add_handler(CommandHandler("agree", cmd_agree))
    app.add_handler(CommandHandler("confirm", cmd_confirm))
    app.add_handler(CommandHandler("inr", cmd_inr))
    app.add_handler(CommandHandler("crp", cmd_crp))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("done", cmd_done))
    
    # Group Control
    app.add_handler(CommandHandler("lock", cmd_lock))
    app.add_handler(CommandHandler("unlock", cmd_unlock))
    
    # User Management
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    
    # Utility
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("close", cmd_close))
    
    # Admin DM
    app.add_handler(CommandHandler("setinrphoto", cmd_setinrphoto))
    app.add_handler(CommandHandler("editcrp", cmd_editcrp))
    app.add_handler(CommandHandler("setvouch", cmd_setvouch))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    
    # Callback
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_dm_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, handle_group_message))
    
    # Chat member handler
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    logger.info("Bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
# FINAL SCRIPT — DUAL-CLIENT BOT WITH ENRICHED MENU (Year / Quality / Language)
# - Bot renders inline menus in the group
# - User account talks to TARGET_BOT (bots cannot DM bots)
# - Enriched button labels while preserving existing behavior
# - Ignores forwarded/media/bot messages to avoid accidental triggers

import io
from telethon.tl import types
import os
import asyncio
import logging
import re
from telethon import TelegramClient, events, Button
from telethon.errors import TimeoutError

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
TARGET_BOT_USERNAME = "ProSearchM5Bot"
MAX_PAGES_TO_SEARCH = 20

if not all([API_ID, API_HASH, BOT_TOKEN]):
    raise ValueError("Missing one or more required environment variables (API_ID, API_HASH, BOT_TOKEN)")

logging.basicConfig(
    format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
    level=logging.INFO
)

# Two clients:
user_client = TelegramClient('user_session', API_ID, API_HASH)              # your personal account (OTP on first run)
bot_client  = TelegramClient('bot_session',  API_ID, API_HASH).start(bot_token=BOT_TOKEN)  # the UI bot

# ---------- Utilities ----------
LANG_WORDS = [
    "hindi", "english", "telugu", "tamil", "malayalam", "kannada",
    "bengali", "marathi", "punjabi", "gujarati", "odia", "oriya",
    "dual", "multi", "multiaudio", "hin+eng", "hin-eng", "tam+tel",
    "dubbed", "hindidub", "hindub"
]

def extract_year(text: str):
    m = re.search(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)', text)
    return m.group(1) if m else None

def extract_lang(text: str):
    t = text.lower()
    langs = []
    for w in LANG_WORDS:
        if w in t:
            if w in ("dual", "multi", "multiaudio"): langs.append("Multi")
            elif w in ("odia", "oriya"): langs.append("Odia")
            elif w in ("hindidub", "hindub"): langs.append("Hindi Dub")
            else: langs.append(w.capitalize())
    seen, out = set(), []
    for l in langs:
        if l not in seen:
            seen.add(l); out.append(l)
    return ", ".join(out) if out else None

def get_quality_label(text: str):
    t = text.lower()
    if any(q in t for q in ("2160p", "4k")): return "2160p"
    if "1080p" in t: return "1080p"
    if "720p"  in t: return "720p"
    if "480p"  in t: return "480p"
    if "hdrip" in t: return "HDRip"
    return None

def quality_rank(q: str) -> int:
    return {"2160p":4, "1080p":3, "720p":2, "480p":1, "HDRip":0}.get(q or "", -1)

def normalize_title(text: str) -> str:
    t = text.lower()
    t = re.sub(r'^\[.*?\]\s*', '', t)
    match = re.search(r'^(.*?)(?:\s\(?\d{4}\)?|\s\d{3,4}p|\s(?:hindi|telugu|tamil|malayalam|kannada|english|bengali|marathi|punjabi|gujarati|odia|oriya))', t)
    if match:
        title = match.group(1).strip()
        title = re.sub(r'\s-\s(part|the)\s\d', '', title, flags=re.IGNORECASE)
        title = re.sub(r':\s(the|part)\s\w+', '', title, flags=re.IGNORECASE)
        cleaned = title.strip().title() if title else t.strip().title()
        return cleaned
    return t.strip().title()

def build_button_label(title: str, year: str, quality: str, lang: str) -> str:
    parts = [title]
    if year: parts.append(year)
    if quality: parts.append(quality)
    if lang: parts.append(lang)
    label = " - ".join(parts)
    MAX_LEN = 60
    if len(label) > MAX_LEN:
        label = label[:MAX_LEN-1].rstrip() + "…"
    return label

def sanitize_button_text_keep_basic_punct(text: str) -> str:
    s = re.sub(r'[^A-Za-z0-9 \-\(\),\+\&\.\:]', '', text).strip()
    return s if s else "Untitled"

# ---------- Discovery (build menu) ----------
async def discovery_agent(chat_id: int, message_id: int, search_query: str):
    status = await bot_client.send_message(chat_id, f"Discovering movies for “{search_query}”...", reply_to=message_id)
    try:
        distinct = {}  # title -> {page,index,qrank,qlabel,year,lang}
        search_words = search_query.lower().split()

        async with user_client.conversation(TARGET_BOT_USERNAME, timeout=180) as conv:
            await conv.send_message(search_query)
            current = await conv.get_response()

            if getattr(current, "buttons", None) and any(k in (current.text or "").lower() for k in ("join", "subscribe")):
                try:
                    await current.click(0)
                    await asyncio.sleep(2)
                    current = await conv.get_response()
                except Exception:
                    pass

            if not (hasattr(current, "buttons") and current.buttons):
                await status.edit(f"Sorry, no results for “{search_query}”.")
                return

            page_num = 1
            while page_num <= MAX_PAGES_TO_SEARCH:
                page_buttons = [b for row in (current.buttons or []) for b in row]
                for i, btn in enumerate(page_buttons):
                    btn_text = (btn.text or "").strip()
                    if not btn_text:
                        continue
                    if not all(w in btn_text.lower() for w in search_words):
                        continue

                    normalized = normalize_title(btn_text)
                    if not normalized:
                        continue

                    year = extract_year(btn_text)
                    qlabel = get_quality_label(btn_text)
                    qrank = quality_rank(qlabel)
                    lang = extract_lang(btn_text)

                    prior = distinct.get(normalized)
                    if (prior is None) or (qrank > prior['qrank']):
                        distinct[normalized] = {
                            'page': page_num,
                            'index': i,
                            'qrank': qrank,
                            'qlabel': qlabel,
                            'year': year,
                            'lang': lang
                        }

                next_btn = next((b for b in page_buttons if (b.text or "").lower().strip().startswith("next")), None)
                if next_btn and page_num < MAX_PAGES_TO_SEARCH:
                    try:
                        await next_btn.click()
                        current = await conv.wait_event(events.MessageEdited(from_users=TARGET_BOT_USERNAME), timeout=15)
                        page_num += 1
                    except TimeoutError:
                        break
                    except Exception:
                        break
                else:
                    break

        if not distinct:
            await status.edit(f"Searched {page_num} pages, but couldn’t find relevant movies for “{search_query}”.")
            return

        # Build enriched buttons (bot sends them)
        buttons = []
        for title, data in distinct.items():
            label = build_button_label(title, data['year'], data['qlabel'], data['lang'])
            safe_label = sanitize_button_text_keep_basic_punct(label)
            
            # --- FIX: Make the callback data smaller (under 64 bytes) ---
            cb = f"get:{data['page']}:{data['index']}"
            
            # Telethon prefers strings for data, it handles the encoding.
            buttons.append([Button.inline(safe_label, data=cb)])

        await status.delete()
        await bot_client.send_message(
            chat_id,
            "I found the following distinct movies. Please choose one:",
            buttons=buttons,
            reply_to=message_id
        )
        logging.info("Menu sent with %d options", len(buttons))

    except Exception as e:
        logging.exception("Discovery error: %s", e)
        try:
            await status.edit("An error occurred during discovery.")
        except Exception:
            pass

def progress_callback(current, total):
    percent = round(current * 100 / total, 1)
    # This will print the progress in your Railway logs
    logging.info("Uploaded %s%%", percent)

async def execution_agent(event: events.CallbackQuery.Event, user_id: int):
    try:
        # For a nice user message, find the text of the button that was clicked
        chosen_title = "your selection"
        reply_message = await event.get_message()
        if reply_message and reply_message.buttons:
            for row in reply_message.buttons:
                for button in row:
                    if button.data == event.data:
                        chosen_title = button.text
                        break
                if chosen_title != "your selection":
                    break
        
        await event.edit(f"Fetching “{chosen_title}”...")

        original_request = await reply_message.get_reply_message()
        if not original_request or not original_request.text:
            await event.edit("Error: Original request not found.")
            return

        data = (event.data or b"").decode('utf-8', errors='ignore')
        _, page_str, index_str = data.split(':', 2)
        target_page = int(page_str)
        target_index = int(index_str)

        # --- PART 1: The User Client (Worker) Gets the File ---
        final_file_message = None
        async with user_client.conversation(TARGET_BOT_USERNAME, timeout=300) as conv: # Increased timeout
            await conv.send_message(original_request.text)
            current = await conv.get_response()

            if getattr(current, "buttons", None) and any(k in (current.text or "").lower() for k in ("join", "subscribe")):
                await current.click(0); await asyncio.sleep(2)
                current = await conv.get_response()

            for _ in range(1, target_page):
                page_buttons = [b for row in (current.buttons or []) for b in row]
                next_btn = next((b for b in page_buttons if (b.text or "").lower().strip().startswith("next")), None)
                if not next_btn: raise Exception("Next button disappeared.")
                await next_btn.click()
                current = await conv.wait_event(events.MessageEdited(from_users=TARGET_BOT_USERNAME), timeout=15)

            await current.click(target_index)

            for _ in range(8):
                resp = await conv.get_response()
                if getattr(resp, "media", None):
                    final_file_message = resp
                    break
        
        if not final_file_message:
            raise TimeoutError("The source bot did not send a file.")

        # --- PART 2: The Bot Client Delivers the File ---
        await event.edit(f"Downloading...")

        # Download the file from the source bot into an in-memory buffer
        file_buffer = io.BytesIO()
        await user_client.download_media(final_file_message, file=file_buffer)
        file_buffer.seek(0)
        logging.info("Download complete. Starting upload to user %d", user_id)
        await event.edit(f"Uploading “{chosen_title}”...")

        # Get the original filename and attributes
        original_filename = "video.mp4"
        file_attributes = []
        if final_file_message.document:
            file_attributes = final_file_message.document.attributes
            for attr in file_attributes:
                if isinstance(attr, types.DocumentAttributeFilename):
                    original_filename = attr.file_name
                    break
        
        # Prepare the final list of attributes for the new file
        final_attributes = [types.DocumentAttributeFilename(original_filename)]
        for attr in file_attributes:
            if not isinstance(attr, types.DocumentAttributeFilename):
                final_attributes.append(attr)

        # The Bot Client sends the file to the user with a progress monitor
        await bot_client.send_file(
            user_id,
            file=file_buffer,
            caption=final_file_message.text,
            attributes=final_attributes,
            force_document=True,
            upload_progress_callback=progress_callback
        )
        
        await event.delete()
        logging.info("Successfully sent file to user %d", user_id)

    except Exception as e:
        logging.exception("Execution error: %s", e)
        try:
            await event.edit("An error occurred during retrieval. Please try again.")
        except Exception:
            pass            
# ---------- BOT LISTENERS ----------
@bot_client.on(events.NewMessage(incoming=True)) # Listen to all incoming messages
async def private_message_listener(event: events.NewMessage.Event):
    
    # --- THIS IS THE FIX ---
    # Only proceed if the message is a private chat (not a group or channel)
    if not event.is_private:
        return

    text = (event.raw_text or "").strip()
    if not text:
        return

    # Ignore forwards/media/bot-sent messages to prevent accidental triggers
    if event.message and (event.message.fwd_from or event.message.media or event.message.via_bot_id):
        return
    sender = await event.get_sender()
    if getattr(sender, 'bot', False):
        return

    if text.startswith('/'):
        return
    if event.is_reply:
        return
    lower = text.lower()
    bot_status_prefixes = (
        "i found the following", "sorry, no results", "an error occurred",
        "fetching", "discovering movies for"
    )
    if any(lower.startswith(p) for p in bot_status_prefixes):
        return

    # If all checks pass, start the discovery process
    asyncio.create_task(discovery_agent(event.chat_id, event.id, text))

@bot_client.on(events.CallbackQuery()) # Listen to all callbacks
async def private_callback_listener(event: events.CallbackQuery.Event):
    
    # Only proceed if the callback is from a private chat
    if not event.is_private:
        await event.answer() # Silently ignore
        return

    data = (event.data or b"").decode(errors='ignore')
    if data.startswith("get:"):
        user_id = event.sender_id
        asyncio.create_task(execution_agent(event, user_id))
    else:
        await event.answer()

# ---------- MAIN ----------
async def main():
    await user_client.start()  # OTP on first run
    me_user = await user_client.get_me()
    me_bot  = await bot_client.get_me()
    logging.info("✅ User client: %s (%s)", me_user.first_name, me_user.id)
    logging.info("✅ Bot client : %s (@%s)", me_bot.first_name, me_bot.username)
    await asyncio.gather(
        bot_client.run_until_disconnected(),
        user_client.run_until_disconnected()
    )

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())










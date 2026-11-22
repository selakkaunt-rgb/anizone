"""
AniZone - anime Telegram bot (Uzbek + Russian).

Stack : Python 3.11+, aiogram 3.x, asyncpg (PostgreSQL via DATABASE_URL), aiohttp keep-alive server.
Roles : user (/start), admin (/admin), owner (/owner).
Deploy: Render free web service + UptimeRobot pinging the keep-alive URL (see README.md).

Everything lives in this single file:
    1. Config            4. Core helpers        7. Admin handlers
    2. Translations      5. Middleware          8. Owner handlers
    3. Database          6. User handlers       9. Web server + entry point
"""
import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    TelegramObject,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("anizone")

# ============================================================================
# 1) CONFIG
# ============================================================================
# Secrets and deployment settings always come from environment variables.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PORT = int(os.getenv("PORT", "10000"))

# ----------------------------------------------------------------------------
# STICKERS - all sticker file_ids live in this single block.
# Get a file_id: open /owner -> "Sticker file_id", then send any sticker to the bot.
# Paste the ids below, or set the STICKER_* environment variables (no redeploy of code needed).
# An empty value simply means "no sticker at this moment".
# Recommended pack style: light line-art, minimal, calm limited palette.
# ----------------------------------------------------------------------------
STICKERS: dict[str, str] = {
    "welcome": os.getenv("STICKER_WELCOME", ""),  # first /start, after language choice
    "success": os.getenv("STICKER_SUCCESS", ""),  # anime added, episodes finished
    "error": os.getenv("STICKER_ERROR", ""),      # episode file unavailable, unexpected failure
    "empty": os.getenv("STICKER_EMPTY", ""),      # nothing found
}

# Tunables
LANGS = ("uz", "ru")
DEFAULT_LANG = "uz"
LIST_PAGE_SIZE = 8          # anime per page in lists
EP_PAGE_SIZE = 25           # episode buttons per page
EP_PER_ROW = 5              # episode buttons per row
PICK_LIMIT = 12             # max results when an admin picks an anime
SUB_CACHE_TTL = 120         # seconds a successful subscription check is trusted
USER_CACHE_TTL = 1800       # seconds a user row stays in memory
BATCH_DEBOUNCE = 1.2        # seconds of silence before a batch of uploaded episodes is saved
SUPPORT_LIMIT = (12, 60)    # at most 12 support messages per 60 seconds per user (albums count per item)
SEP = "─" * 16              # the thin separator line used in every message
FOREVER = datetime(2999, 1, 1, tzinfo=timezone.utc)

# Fixed genre list (keys are translated as g_<key>)
GENRES = [
    "action", "adventure", "comedy", "drama", "fantasy", "romance", "school",
    "scifi", "horror", "mystery", "sports", "isekai", "mecha", "slice",
]

# Runtime globals (filled in main())
BOT: Bot
BOT_USERNAME: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# ============================================================================
# 2) TRANSLATIONS  - every text of the bot lives here, in both languages.
#    Use t(lang, "key", **values). To add a text: add the SAME key to "uz" and "ru"
#    (the bot refuses to start if the two languages differ).
#    Content (anime names, videos, posts) is never translated.
# ============================================================================
TEXTS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------ O'ZBEKCHA
    "uz": {
        # language / main menu
        "btn_lang_uz": "🇺🇿 O'zbekcha",
        "btn_lang_ru": "🇷🇺 Русский",
        "lang_choose": "<b>🌐 Til / Язык</b>\n{sep}\nTilni tanlang · Выберите язык",
        "lang_saved": "✅ Til saqlandi",
        "welcome": "<b>🎬 AniZone</b>\n{sep}\nSalom, {name}! Bu yerda sevimli animelaringizni topib, qulay tomosha qilishingiz mumkin.",
        "menu": "<b>🎬 AniZone</b>\n{sep}\nKerakli bo'limni tanlang.",
        "btn_search": "🔍 Anime izlash",
        "btn_vip": "💎 VIP",
        "btn_about": "ℹ️ Bot haqida",
        "btn_support": "📩 Murojaat",
        "btn_lang": "🌐 Til / Язык",
        "btn_back": "⬅️ Orqaga",
        "btn_home": "🏠 Bosh menyu",
        "btn_cancel": "✖️ Bekor qilish",
        "btn_no": "↩️ Yo'q",
        "btn_yes_delete": "🗑 Ha, o'chirish",
        "btn_check": "✅ Tekshirish",
        "btn_add": "➕ Qo'shish",
        "btn_done": "✅ Tayyor",
        "btn_panel": "🛠 Panel",
        "btn_admin_panel": "🛠 Admin panel",
        "btn_owner_panel": "👑 Owner panel",
        "cmd_start": "Bosh menyu",
        "cmd_admin": "Admin panel",
        "cmd_owner": "Owner panel",
        "done_title": "✅ Tayyor",
        "expired": "Bu tugma eskirgan. /start ni bosing.",
        "unexpected_input": "Iltimos, so'ralgan ma'lumotni yuboring yoki /cancel ni bosing.",
        "no_access": "⛔ Sizda bu buyruq uchun ruxsat yo'q.",
        "err_generic": "⚠️ Xatolik yuz berdi. Keyinroq qayta urinib ko'ring.",
        # search
        "search_menu": "<b>🔍 Anime izlash</b>\n{sep}\nQidiruv usulini tanlang.",
        "btn_s_name": "🔎 Nomi bilan",
        "btn_s_code": "🔢 Kod orqali",
        "btn_s_genre": "🏷 Janr orqali",
        "btn_s_old": "🕰 Eng eski",
        "btn_s_top": "🔥 Top",
        "btn_s_new": "🆕 Eng yangi chiqqan animelar",
        "ask_name": "<b>🔎 Nomi bilan izlash</b>\n{sep}\nAnime nomini yuboring. Nomning bir qismi ham yetarli.",
        "ask_code": "<b>🔢 Kod orqali izlash</b>\n{sep}\nAnime kodini (raqam) yuboring.",
        "genre_menu": "<b>🏷 Janr orqali izlash</b>\n{sep}\nJanrni tanlang.",
        "name_too_short": "✏️ Kamida 2 ta belgi yuboring.",
        "bad_code": "⚠️ Kod faqat raqamlardan iborat bo'lishi kerak.",
        "list_old": "🕰 Eng eski animelar",
        "list_top": "🔥 Top animelar",
        "list_new": "🆕 Eng yangi chiqqan animelar",
        "list_genre": "🏷 Janr: {genre}",
        "list_name": "🔎 Natijalar: {q}",
        "list_code": "🔢 Kod: {code}",
        "list_count": "Topildi: {total} ta. Birini tanlang.",
        "list_empty": "Hech narsa topilmadi.",
        "anime_gone": "Anime topilmadi yoki o'chirilgan.",
        # anime card / episodes
        "card_year": "📅 Yil: {year}",
        "card_code": "🔢 Kod: <code>{code}</code>",
        "card_genres": "🏷 Janr: {genres}",
        "card_eps": "🎞 Qismlar: {n}",
        "card_no_eps": "🎞 Qismlar hali qo'shilmagan",
        "ep_caption": "🎞 {n}-qism",
        "ep_missing": "Bu qism topilmadi.",
        "ep_unavailable": "⚠️ Videoni yuborib bo'lmadi. Keyinroq urinib ko'ring.",
        "btn_all_eps": "📋 Barcha qismlar",
        "btn_open_bot": "▶️ Botda ko'rish",
        "post_new_eps": "🆕 Yangi qism: {eps}",
        # VIP / about
        "vip_title": "💎 VIP",
        "vip_soon": "Tez orada! VIP bo'limi tayyorlanmoqda. Yangiliklarni kuzatib boring.",
        "vip_active": "✅ VIP faol: {until} gacha",
        "vip_received": "Sizga VIP berildi! Amal qilish muddati: {until}.",
        "vip_removed": "VIP statusingiz olib tashlandi.",
        "about_title": "ℹ️ Bot haqida",
        "about_default": "AniZone — sevimli animelaringizni qulay topish va tomosha qilish uchun bot. Nom, kod yoki janr bo'yicha izlang va qismlarni shu yerning o'zida ko'ring.\n\nSavollar bo'lsa, «📩 Murojaat» orqali yozing.",
        # support
        "support_prompt": "<b>📩 Murojaat</b>\n{sep}\nXabaringizni yozing, adminlarga yetkazamiz. Matn yoki media yuborishingiz mumkin.",
        "support_sent": "✅ Xabaringiz yuborildi. Javob shu yerga keladi.",
        "support_fail": "⚠️ Hozircha xabarni yetkazib bo'lmadi. Keyinroq urinib ko'ring.",
        "support_rate": "⏳ Juda tez yuboryapsiz. Birozdan keyin urinib ko'ring.",
        "sup_header": "<b>📩 Yangi murojaat</b>\n{sep}\n👤 {who}\n🆔 <code>{uid}</code>",
        "btn_reply": "↩️ Javob berish",
        "reply_prompt": "<b>↩️ Javob</b>\n{sep}\n<code>{uid}</code> uchun javobingizni yuboring (matn yoki media).",
        "reply_title": "💬 Admin javobi",
        "reply_sent": "✅ Javob yuborildi.",
        "reply_failed": "⚠️ Javobni yetkazib bo'lmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).",
        "btn_write_again": "✍️ Yana yozish",
        # forced subscription
        "sub_text": "<b>🔒 Obuna kerak</b>\n{sep}\nBotdan foydalanish uchun quyidagi kanallarga a'zo bo'ling, so'ng «Tekshirish» tugmasini bosing.",
        "sub_not_yet": "Hali hammasiga a'zo bo'lmagansiz.",
        "sub_ok": "✅ Rahmat!",
        # admin panel
        "admin_title": "🛠 Admin panel",
        "admin_hint": "Kerakli amalni tanlang.",
        "a_add_anime": "➕ Anime qo'shish",
        "a_del_anime": "🗑 Anime o'chirish",
        "a_add_ep": "🎞 Qism qo'shish",
        "a_del_ep": "❌ Qism o'chirish",
        "a_post": "📝 Post qo'shish",
        "a_auto": "🤖 Avtomatik post",
        "pick_title_add_ep": "🎞 Qism qo'shish",
        "pick_title_del_ep": "❌ Qism o'chirish",
        "pick_title_del_anime": "🗑 Anime o'chirish",
        "pick_title_post": "📝 Post qo'shish",
        "pick_prompt": "Anime ID (kod) yoki nomini yuboring.",
        "pick_none": "Hech narsa topilmadi. Boshqa ID yoki nom yuboring.",
        "pick_results": "🔎 Natijalar",
        "pick_choose": "Animeni tanlang.",
        "pick_more": "Natijalar ko'p, birinchilari ko'rsatildi. Aniqroq nom yuboring.",
        # add anime
        "add_ask_mode": "ID turini tanlang:\n• Ketma-ket: avtomatik 1, 2, 3...\n• Maxsus ID: o'zingiz yozasiz",
        "btn_id_seq": "🔢 Ketma-ket",
        "btn_id_custom": "✏️ Maxsus ID",
        "add_ask_id": "Maxsus ID (raqam) yuboring. Bir ID ostida bir nechta anime bo'lishi mumkin.",
        "add_bad_id": "⚠️ ID faqat raqam bo'lishi kerak (1 dan boshlab).",
        "add_ask_name": "Anime nomini yuboring.",
        "add_bad_name": "⚠️ Nom 1 dan 200 gacha belgidan iborat bo'lishi kerak.",
        "add_ask_poster": "Poster yuboring: rasm yoki video.",
        "add_ask_year": "Chiqqan yilini yuboring (masalan, 2019).",
        "add_bad_year": "⚠️ Yilni 4 xonali raqam bilan yuboring (masalan, 2019).",
        "add_saved": "Anime saqlandi. Kod: <code>{code}</code>",
        "add_shared": "ℹ️ Bu kod ostida endi {n} ta anime bor.",
        "btn_genres": "🏷 Janr belgilash",
        "genre_hint": "Janrlarni tanlang",
        "genre_saved": "Saqlandi",
        # delete anime
        "del_confirm": "<b>{name}</b> ({year}), kod {code}\nQismlar: {n}\n\nO'chirilsa, barcha qismlari ham o'chadi. Bu amalni qaytarib bo'lmaydi.",
        "deleted_anime": "✅ Anime o'chirildi.",
        # add episodes
        "ep_mode_text": "<b>{name}</b>\nMavjud qismlar: {n} (oxirgisi: {last})\n\nRejimni tanlang:\n• Ketma-ket: videolarni birin-ketin yuborasiz, raqamlar avtomatik beriladi\n• Maxsus: qism raqamini o'zingiz tanlaysiz",
        "btn_mode_seq": "📥 Ketma-ket",
        "btn_mode_custom": "🎯 Maxsus",
        "ep_seq_intro": "<b>{name}</b>\nVideolarni birin-ketin yuboring. Birinchisi {next}-qism bo'ladi.\n\nTugagach «Tugatish» ni bosing.",
        "ep_seq_status": "<b>{name}</b>\n📥 Qo'shildi: {n} ta ({eps})\n\nDavom eting yoki «Tugatish» ni bosing.",
        "ep_seq_failed": "<b>{name}</b>\n⚠️ Qismlarni saqlashda xatolik. Videolarni qayta yuboring.",
        "btn_finish": "✅ Tugatish",
        "ep_need_video": "⚠️ Video yuboring (yoki video fayl).",
        "ep_custom_intro": "<b>{name}</b>\nMavjud: {have}\n\nQism raqamini yuboring.",
        "ep_bad_number": "⚠️ Qism raqamini yuboring (faqat raqam, 1 dan boshlab).",
        "ep_custom_video": "Endi {n}-qism videosini yuboring.",
        "ep_custom_video_replace": "⚠️ {n}-qism mavjud. Yangi video uni almashtiradi. Videoni yuboring.",
        "ep_custom_saved": "✅ {n}-qism saqlandi. Keyingi qism raqamini yuboring yoki «Tugatish» ni bosing.",
        "ep_custom_saved_replaced": "✅ {n}-qism almashtirildi. Keyingi qism raqamini yuboring yoki «Tugatish» ni bosing.",
        "ep_none_added": "Hech qanday qism qo'shilmadi.",
        "ep_done": "Qo'shilgan qismlar: {n} ta ({eps}).",
        # delete episode
        "delep_none": "<b>{name}</b> uchun qismlar yo'q.",
        "delep_prompt": "<b>{name}</b>\nMavjud: {eps}\n\nO'chiriladigan qism raqamini yuboring.",
        "delep_missing": "⚠️ {n}-qism topilmadi.",
        "delep_confirm": "<b>{name}</b>: {n}-qism o'chirilsinmi?",
        "delep_done": "✅ {n}-qism o'chirildi.",
        "btn_del_more": "❌ Yana qism o'chirish",
        # posts
        "btn_post_send": "📤 Kanallarga yuborish",
        "post_sending": "Yuborilmoqda…",
        "post_sent": "📣 Kanallarga yuborildi: {ok}/{total}",
        "post_no_channels": "Post kanallari qo'shilmagan. Owner ularni /owner orqali qo'shishi kerak.",
        "auto_on": "🟢 Avtomatik post yoqilgan.",
        "auto_off": "🔴 Avtomatik post o'chirilgan.",
        "auto_info": "Yoqilganda yangi anime qo'shilganda va qismlar «Tugatish» bilan yakunlanganda post kanallarga avtomatik yuboriladi.\nPost kanallari: {n} ta.",
        "btn_turn_on": "🟢 Yoqish",
        "btn_turn_off": "🔴 O'chirish",
        # owner panel
        "owner_title": "👑 Owner panel",
        "owner_hint": "Bo'limni tanlang.",
        "o_post_channels": "📣 Post kanallari",
        "o_sub_channels": "🔒 Majburiy obuna",
        "o_vip": "💎 VIP",
        "o_about": "ℹ️ Bot haqida matni",
        "o_admins": "👮 Adminlar",
        "o_sticker": "🧩 Sticker file_id",
        "ch_hint_post": "Postlar yuboriladigan kanallar. Bot kanalda admin bo'lishi kerak.\n«🗑» tugmasi kanalni o'chiradi.",
        "ch_hint_sub": "Foydalanuvchilar a'zo bo'lishi shart bo'lgan kanallar.\n«🗑» tugmasi kanalni o'chiradi.",
        "ch_count": "Kanallar: {n} ta.",
        "ch_empty": "Hali qo'shilmagan.",
        "ch_add_prompt": "Kanaldan istalgan xabarni shu yerga forward qiling yoki @username / ID yuboring.\nBot kanalda admin bo'lishi shart.",
        "ch_err_ref": "⚠️ Kanalni aniqlab bo'lmadi. Kanaldan xabar forward qiling yoki @username / ID yuboring.",
        "ch_err_notfound": "⚠️ Kanal topilmadi. @username yoki ID ni tekshiring.",
        "ch_err_type": "⚠️ Bu kanal yoki guruh emas.",
        "ch_err_admin": "⚠️ Bot bu kanalda admin emas. Avval botni admin qiling.",
        "ch_err_post_right": "⚠️ Botda xabar yuborish huquqi yo'q.",
        "ch_err_link": "⚠️ Taklif havolasini olib bo'lmadi. Botga foydalanuvchilarni taklif qilish huquqini bering yoki ochiq (@username) kanal ishlating.",
        "ch_ask_lang": "Bu kanalga postlar qaysi tilda yuborilsin?",
        "ch_added": "✅ Qo'shildi: {title}",
        "ch_del_confirm": "Kanal ro'yxatdan o'chirilsinmi?",
        "ch_deleted": "O'chirildi",
        "vip_owner_hint": "VIP berish yoki foydalanuvchilarga ko'rinadigan narx / to'lov matnini tahrirlash.\nVIP egalari majburiy obunadan ozod bo'ladi.",
        "btn_vip_give": "🎁 VIP berish",
        "btn_vip_text": "💰 VIP matni",
        "vip_ask_user": "Foydalanuvchi ID yoki @username yuboring (u botni ishga tushirgan bo'lishi kerak).",
        "vip_user_unknown": "⚠️ Foydalanuvchi topilmadi. U avval botda /start bosishi kerak.",
        "vip_choose": "Foydalanuvchi: {who}\nID: <code>{uid}</code>\n\nMuddatni tanlang.",
        "vip_current": "Hozirgi VIP: {until} gacha",
        "vip_current_none": "Hozir VIP emas.",
        "btn_days": "{n} kun",
        "btn_forever": "♾ Cheksiz",
        "btn_vip_remove": "🚫 Olib tashlash",
        "vip_given": "✅ VIP berildi: {until} gacha.",
        "vip_removed_owner": "✅ VIP olib tashlandi.",
        "about_owner_hint": "Foydalanuvchilarga ko'rinadigan matnni har bir til uchun alohida yozing. Bo'sh qoldirilsa, standart matn ko'rsatiladi.",
        "text_prompt": "{flag} Hozirgi matn:\n{current}\n\nYangi matnni yuboring (formatlash saqlanadi).",
        "text_empty": "— bo'sh —",
        "text_saved": "✅ Saqlandi.",
        "text_cleared": "✅ Tozalandi.",
        "text_too_long": "⚠️ Matn juda uzun (3000 belgigacha).",
        "btn_clear": "🧹 Tozalash",
        "adm_empty": "Adminlar yo'q.",
        "adm_count": "Adminlar: {n} ta.",
        "adm_ask": "Yangi adminning ID sini yoki @username ini yuboring.",
        "adm_unknown": "⚠️ Foydalanuvchi topilmadi. Raqamli ID yuboring.",
        "adm_exists": "ℹ️ Bu foydalanuvchi allaqachon admin.",
        "adm_added": "✅ Admin qo'shildi: <code>{uid}</code>",
        "adm_del_confirm": "<code>{uid}</code> adminlikdan olinsinmi?",
        "adm_deleted": "Olib tashlandi",
        "adm_notice_title": "🛠 Admin",
        "adm_notice": "Sizga admin huquqi berildi. /admin ni bosing.",
        "sticker_prompt": "Istalgan stikerni yuboring, men uning file_id sini qaytaraman.",
        "sticker_id": "<code>{fid}</code>\n\nBu qiymatni kodning STICKERS blokiga yoki STICKER_* o'zgaruvchisiga qo'ying.",
        # genres
        "g_action": "Jangari",
        "g_adventure": "Sarguzasht",
        "g_comedy": "Komediya",
        "g_drama": "Drama",
        "g_fantasy": "Fentezi",
        "g_romance": "Romantika",
        "g_school": "Maktab",
        "g_scifi": "Ilmiy fantastika",
        "g_horror": "Dahshat",
        "g_mystery": "Detektiv",
        "g_sports": "Sport",
        "g_isekai": "Isekai",
        "g_mecha": "Mecha",
        "g_slice": "Kundalik hayot",
    },
    # ------------------------------------------------------------------ РУССКИЙ
    "ru": {
        "btn_lang_uz": "🇺🇿 O'zbekcha",
        "btn_lang_ru": "🇷🇺 Русский",
        "lang_choose": "<b>🌐 Til / Язык</b>\n{sep}\nTilni tanlang · Выберите язык",
        "lang_saved": "✅ Язык сохранён",
        "welcome": "<b>🎬 AniZone</b>\n{sep}\nПривет, {name}! Здесь можно легко найти любимые аниме и смотреть их прямо в боте.",
        "menu": "<b>🎬 AniZone</b>\n{sep}\nВыберите нужный раздел.",
        "btn_search": "🔍 Поиск аниме",
        "btn_vip": "💎 VIP",
        "btn_about": "ℹ️ О боте",
        "btn_support": "📩 Обращение",
        "btn_lang": "🌐 Til / Язык",
        "btn_back": "⬅️ Назад",
        "btn_home": "🏠 Главное меню",
        "btn_cancel": "✖️ Отмена",
        "btn_no": "↩️ Нет",
        "btn_yes_delete": "🗑 Да, удалить",
        "btn_check": "✅ Проверить",
        "btn_add": "➕ Добавить",
        "btn_done": "✅ Готово",
        "btn_panel": "🛠 Панель",
        "btn_admin_panel": "🛠 Админ-панель",
        "btn_owner_panel": "👑 Панель владельца",
        "cmd_start": "Главное меню",
        "cmd_admin": "Админ-панель",
        "cmd_owner": "Панель владельца",
        "done_title": "✅ Готово",
        "expired": "Эта кнопка устарела. Нажмите /start.",
        "unexpected_input": "Пожалуйста, отправьте запрошенные данные или нажмите /cancel.",
        "no_access": "⛔ У вас нет доступа к этой команде.",
        "err_generic": "⚠️ Произошла ошибка. Попробуйте позже.",
        "search_menu": "<b>🔍 Поиск аниме</b>\n{sep}\nВыберите способ поиска.",
        "btn_s_name": "🔎 По названию",
        "btn_s_code": "🔢 По коду",
        "btn_s_genre": "🏷 По жанру",
        "btn_s_old": "🕰 Старые",
        "btn_s_top": "🔥 Топ",
        "btn_s_new": "🆕 Самые новые аниме",
        "ask_name": "<b>🔎 Поиск по названию</b>\n{sep}\nОтправьте название аниме. Достаточно части названия.",
        "ask_code": "<b>🔢 Поиск по коду</b>\n{sep}\nОтправьте код аниме (число).",
        "genre_menu": "<b>🏷 Поиск по жанру</b>\n{sep}\nВыберите жанр.",
        "name_too_short": "✏️ Отправьте минимум 2 символа.",
        "bad_code": "⚠️ Код должен состоять только из цифр.",
        "list_old": "🕰 Самые старые аниме",
        "list_top": "🔥 Топ аниме",
        "list_new": "🆕 Самые новые аниме",
        "list_genre": "🏷 Жанр: {genre}",
        "list_name": "🔎 Результаты: {q}",
        "list_code": "🔢 Код: {code}",
        "list_count": "Найдено: {total}. Выберите аниме.",
        "list_empty": "Ничего не найдено.",
        "anime_gone": "Аниме не найдено или удалено.",
        "card_year": "📅 Год: {year}",
        "card_code": "🔢 Код: <code>{code}</code>",
        "card_genres": "🏷 Жанр: {genres}",
        "card_eps": "🎞 Серии: {n}",
        "card_no_eps": "🎞 Серии ещё не добавлены",
        "ep_caption": "🎞 Серия {n}",
        "ep_missing": "Эта серия не найдена.",
        "ep_unavailable": "⚠️ Не удалось отправить видео. Попробуйте позже.",
        "btn_all_eps": "📋 Все серии",
        "btn_open_bot": "▶️ Смотреть в боте",
        "post_new_eps": "🆕 Новые серии: {eps}",
        "vip_title": "💎 VIP",
        "vip_soon": "Скоро! Раздел VIP в разработке. Следите за новостями.",
        "vip_active": "✅ VIP активен до {until}",
        "vip_received": "Вам выдан VIP! Срок действия: {until}.",
        "vip_removed": "Ваш VIP-статус снят.",
        "about_title": "ℹ️ О боте",
        "about_default": "AniZone — бот для удобного поиска и просмотра любимых аниме. Ищите по названию, коду или жанру и смотрите серии прямо здесь.\n\nЕсть вопросы? Напишите через «📩 Обращение».",
        "support_prompt": "<b>📩 Обращение</b>\n{sep}\nНапишите сообщение, и мы передадим его администраторам. Можно отправить текст или медиа.",
        "support_sent": "✅ Сообщение отправлено. Ответ придёт сюда.",
        "support_fail": "⚠️ Сейчас не удалось доставить сообщение. Попробуйте позже.",
        "support_rate": "⏳ Слишком часто. Попробуйте чуть позже.",
        "sup_header": "<b>📩 Новое обращение</b>\n{sep}\n👤 {who}\n🆔 <code>{uid}</code>",
        "btn_reply": "↩️ Ответить",
        "reply_prompt": "<b>↩️ Ответ</b>\n{sep}\nОтправьте ответ для <code>{uid}</code> (текст или медиа).",
        "reply_title": "💬 Ответ администратора",
        "reply_sent": "✅ Ответ отправлен.",
        "reply_failed": "⚠️ Не удалось доставить ответ (возможно, пользователь заблокировал бота).",
        "btn_write_again": "✍️ Написать ещё",
        "sub_text": "<b>🔒 Нужна подписка</b>\n{sep}\nЧтобы пользоваться ботом, подпишитесь на каналы ниже, затем нажмите «Проверить».",
        "sub_not_yet": "Вы подписались ещё не на все каналы.",
        "sub_ok": "✅ Спасибо!",
        "admin_title": "🛠 Админ-панель",
        "admin_hint": "Выберите действие.",
        "a_add_anime": "➕ Добавить аниме",
        "a_del_anime": "🗑 Удалить аниме",
        "a_add_ep": "🎞 Добавить серию",
        "a_del_ep": "❌ Удалить серию",
        "a_post": "📝 Добавить пост",
        "a_auto": "🤖 Автопост",
        "pick_title_add_ep": "🎞 Добавить серию",
        "pick_title_del_ep": "❌ Удалить серию",
        "pick_title_del_anime": "🗑 Удалить аниме",
        "pick_title_post": "📝 Добавить пост",
        "pick_prompt": "Отправьте ID (код) или название аниме.",
        "pick_none": "Ничего не найдено. Отправьте другой ID или название.",
        "pick_results": "🔎 Результаты",
        "pick_choose": "Выберите аниме.",
        "pick_more": "Результатов много, показаны первые. Уточните название.",
        "add_ask_mode": "Выберите тип ID:\n• Последовательный: автоматически 1, 2, 3...\n• Свой ID: вводите сами",
        "btn_id_seq": "🔢 Последовательный",
        "btn_id_custom": "✏️ Свой ID",
        "add_ask_id": "Отправьте свой ID (число). Под одним ID может быть несколько аниме.",
        "add_bad_id": "⚠️ ID должен быть числом (начиная с 1).",
        "add_ask_name": "Отправьте название аниме.",
        "add_bad_name": "⚠️ Название должно содержать от 1 до 200 символов.",
        "add_ask_poster": "Отправьте постер: фото или видео.",
        "add_ask_year": "Отправьте год выпуска (например, 2019).",
        "add_bad_year": "⚠️ Отправьте год четырьмя цифрами (например, 2019).",
        "add_saved": "Аниме сохранено. Код: <code>{code}</code>",
        "add_shared": "ℹ️ Под этим кодом теперь {n} аниме.",
        "btn_genres": "🏷 Указать жанр",
        "genre_hint": "Выберите жанры",
        "genre_saved": "Сохранено",
        "del_confirm": "<b>{name}</b> ({year}), код {code}\nСерий: {n}\n\nПри удалении все серии тоже будут удалены. Это действие необратимо.",
        "deleted_anime": "✅ Аниме удалено.",
        "ep_mode_text": "<b>{name}</b>\nСерий: {n} (последняя: {last})\n\nВыберите режим:\n• Последовательный: отправляете видео одно за другим, номера присваиваются автоматически\n• Свой номер: номер серии выбираете сами",
        "btn_mode_seq": "📥 Последовательно",
        "btn_mode_custom": "🎯 Свой номер",
        "ep_seq_intro": "<b>{name}</b>\nОтправляйте видео одно за другим. Первое станет серией {next}.\n\nКогда закончите, нажмите «Завершить».",
        "ep_seq_status": "<b>{name}</b>\n📥 Добавлено: {n} ({eps})\n\nПродолжайте или нажмите «Завершить».",
        "ep_seq_failed": "<b>{name}</b>\n⚠️ Ошибка при сохранении серий. Отправьте видео ещё раз.",
        "btn_finish": "✅ Завершить",
        "ep_need_video": "⚠️ Отправьте видео (или видеофайл).",
        "ep_custom_intro": "<b>{name}</b>\nЕсть: {have}\n\nОтправьте номер серии.",
        "ep_bad_number": "⚠️ Отправьте номер серии (только число, начиная с 1).",
        "ep_custom_video": "Теперь отправьте видео серии {n}.",
        "ep_custom_video_replace": "⚠️ Серия {n} уже есть. Новое видео заменит её. Отправьте видео.",
        "ep_custom_saved": "✅ Серия {n} сохранена. Отправьте номер следующей серии или нажмите «Завершить».",
        "ep_custom_saved_replaced": "✅ Серия {n} заменена. Отправьте номер следующей серии или нажмите «Завершить».",
        "ep_none_added": "Ни одной серии не добавлено.",
        "ep_done": "Добавлено серий: {n} ({eps}).",
        "delep_none": "У <b>{name}</b> нет серий.",
        "delep_prompt": "<b>{name}</b>\nЕсть: {eps}\n\nОтправьте номер серии для удаления.",
        "delep_missing": "⚠️ Серия {n} не найдена.",
        "delep_confirm": "<b>{name}</b>: удалить серию {n}?",
        "delep_done": "✅ Серия {n} удалена.",
        "btn_del_more": "❌ Удалить ещё серию",
        "btn_post_send": "📤 Отправить в каналы",
        "post_sending": "Отправляю…",
        "post_sent": "📣 Отправлено в каналы: {ok}/{total}",
        "post_no_channels": "Каналы для постов не добавлены. Владелец может добавить их через /owner.",
        "auto_on": "🟢 Автопост включён.",
        "auto_off": "🔴 Автопост выключен.",
        "auto_info": "Когда включён, пост уходит в каналы автоматически: при добавлении аниме и при завершении добавления серий кнопкой «Завершить».\nКаналов для постов: {n}.",
        "btn_turn_on": "🟢 Включить",
        "btn_turn_off": "🔴 Выключить",
        "owner_title": "👑 Панель владельца",
        "owner_hint": "Выберите раздел.",
        "o_post_channels": "📣 Каналы для постов",
        "o_sub_channels": "🔒 Обязательная подписка",
        "o_vip": "💎 VIP",
        "o_about": "ℹ️ Текст «О боте»",
        "o_admins": "👮 Админы",
        "o_sticker": "🧩 Sticker file_id",
        "ch_hint_post": "Каналы, куда публикуются посты. Бот должен быть администратором канала.\nКнопка «🗑» удаляет канал.",
        "ch_hint_sub": "Каналы, на которые пользователи обязаны подписаться.\nКнопка «🗑» удаляет канал.",
        "ch_count": "Каналов: {n}.",
        "ch_empty": "Пока не добавлено.",
        "ch_add_prompt": "Перешлите сюда любое сообщение из канала или отправьте @username / ID.\nБот должен быть администратором канала.",
        "ch_err_ref": "⚠️ Не удалось определить канал. Перешлите сообщение из канала или отправьте @username / ID.",
        "ch_err_notfound": "⚠️ Канал не найден. Проверьте @username или ID.",
        "ch_err_type": "⚠️ Это не канал и не группа.",
        "ch_err_admin": "⚠️ Бот не является администратором этого канала. Сначала сделайте его админом.",
        "ch_err_post_right": "⚠️ У бота нет права публиковать сообщения.",
        "ch_err_link": "⚠️ Не удалось получить пригласительную ссылку. Дайте боту право приглашать пользователей или используйте публичный канал (@username).",
        "ch_ask_lang": "На каком языке публиковать посты в этом канале?",
        "ch_added": "✅ Добавлено: {title}",
        "ch_del_confirm": "Удалить канал из списка?",
        "ch_deleted": "Удалено",
        "vip_owner_hint": "Выдача VIP или редактирование текста с ценой / способом оплаты, который видят пользователи.\nВладельцы VIP освобождаются от обязательной подписки.",
        "btn_vip_give": "🎁 Выдать VIP",
        "btn_vip_text": "💰 Текст VIP",
        "vip_ask_user": "Отправьте ID или @username пользователя (он должен был запустить бота).",
        "vip_user_unknown": "⚠️ Пользователь не найден. Он должен сначала нажать /start в боте.",
        "vip_choose": "Пользователь: {who}\nID: <code>{uid}</code>\n\nВыберите срок.",
        "vip_current": "Текущий VIP: до {until}",
        "vip_current_none": "Сейчас не VIP.",
        "btn_days": "{n} дн.",
        "btn_forever": "♾ Навсегда",
        "btn_vip_remove": "🚫 Снять",
        "vip_given": "✅ VIP выдан до {until}.",
        "vip_removed_owner": "✅ VIP снят.",
        "about_owner_hint": "Напишите текст для пользователей отдельно для каждого языка. Если оставить пустым, будет показан стандартный текст.",
        "text_prompt": "{flag} Текущий текст:\n{current}\n\nОтправьте новый текст (форматирование сохранится).",
        "text_empty": "— пусто —",
        "text_saved": "✅ Сохранено.",
        "text_cleared": "✅ Очищено.",
        "text_too_long": "⚠️ Текст слишком длинный (до 3000 символов).",
        "btn_clear": "🧹 Очистить",
        "adm_empty": "Админов нет.",
        "adm_count": "Админов: {n}.",
        "adm_ask": "Отправьте ID или @username нового админа.",
        "adm_unknown": "⚠️ Пользователь не найден. Отправьте числовой ID.",
        "adm_exists": "ℹ️ Этот пользователь уже админ.",
        "adm_added": "✅ Админ добавлен: <code>{uid}</code>",
        "adm_del_confirm": "Снять <code>{uid}</code> с должности админа?",
        "adm_deleted": "Снят",
        "adm_notice_title": "🛠 Админ",
        "adm_notice": "Вам выданы права администратора. Нажмите /admin.",
        "sticker_prompt": "Отправьте любой стикер, и я верну его file_id.",
        "sticker_id": "<code>{fid}</code>\n\nВставьте это значение в блок STICKERS в коде или в переменную STICKER_*.",
        "g_action": "Боевик",
        "g_adventure": "Приключения",
        "g_comedy": "Комедия",
        "g_drama": "Драма",
        "g_fantasy": "Фэнтези",
        "g_romance": "Романтика",
        "g_school": "Школа",
        "g_scifi": "Научная фантастика",
        "g_horror": "Ужасы",
        "g_mystery": "Детектив",
        "g_sports": "Спорт",
        "g_isekai": "Исекай",
        "g_mecha": "Меха",
        "g_slice": "Повседневность",
    },
}

# ============================================================================
# 3) DATABASE (PostgreSQL through DATABASE_URL)
# ============================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    BIGINT PRIMARY KEY,
    lang       TEXT,
    username   TEXT,
    full_name  TEXT,
    vip_until  TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS admins (
    user_id  BIGINT PRIMARY KEY,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS animes (
    uid         BIGSERIAL PRIMARY KEY,
    code        BIGINT NOT NULL,
    name        TEXT NOT NULL,
    poster_id   TEXT NOT NULL,
    poster_type TEXT NOT NULL DEFAULT 'photo',
    year        INT NOT NULL,
    genres      TEXT[] NOT NULL DEFAULT '{}',
    views       BIGINT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_animes_code  ON animes (code);
CREATE INDEX IF NOT EXISTS idx_animes_year  ON animes (year);
CREATE INDEX IF NOT EXISTS idx_animes_views ON animes (views DESC);
CREATE TABLE IF NOT EXISTS episodes (
    id         BIGSERIAL PRIMARY KEY,
    anime_uid  BIGINT NOT NULL REFERENCES animes (uid) ON DELETE CASCADE,
    number     INT NOT NULL,
    file_id    TEXT NOT NULL,
    file_type  TEXT NOT NULL DEFAULT 'video',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (anime_uid, number)
);
CREATE TABLE IF NOT EXISTS channels (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,
    chat_id     BIGINT NOT NULL,
    title       TEXT NOT NULL,
    username    TEXT,
    invite_link TEXT,
    lang        TEXT NOT NULL DEFAULT 'uz',
    UNIQUE (kind, chat_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

pool: Optional[asyncpg.Pool] = None
_RETRYABLE = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)


def _clean_dsn(url: str) -> str:
    """Keep only the query parameters asyncpg understands (Neon/Supabase add extras)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k == "sslmode"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def init_db() -> None:
    global pool
    pool = await asyncpg.create_pool(
        dsn=_clean_dsn(DATABASE_URL),
        min_size=1,
        max_size=5,
        command_timeout=30,
        statement_cache_size=0,  # safe with pgbouncer / pooled endpoints
        max_inactive_connection_lifetime=60,
    )
    async with pool.acquire() as con:
        await con.execute(SCHEMA)
    log.info("Database ready")


async def close_db() -> None:
    if pool is not None:
        await pool.close()


async def _with_conn(fn: Callable[[asyncpg.Connection], Awaitable[Any]]) -> Any:
    """Run fn(connection); retry a few times on dropped connections (idle sleep, restarts)."""
    for attempt in range(3):
        try:
            async with pool.acquire() as con:
                return await fn(con)
        except _RETRYABLE as e:
            if attempt == 2:
                raise
            log.warning("DB connection problem (%s), retrying", e)
            await asyncio.sleep(0.6 * (attempt + 1))


async def q_fetch(sql: str, *args) -> list:
    return await _with_conn(lambda con: con.fetch(sql, *args))


async def q_row(sql: str, *args):
    return await _with_conn(lambda con: con.fetchrow(sql, *args))


async def q_val(sql: str, *args):
    return await _with_conn(lambda con: con.fetchval(sql, *args))


async def q_exec(sql: str, *args) -> str:
    return await _with_conn(lambda con: con.execute(sql, *args))


# ---------------------------------------------------------------- users
async def db_upsert_user(uid: int, username: Optional[str], full_name: str):
    return await q_row(
        """INSERT INTO users (user_id, username, full_name) VALUES ($1, $2, $3)
           ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, full_name = EXCLUDED.full_name
           RETURNING user_id, lang, vip_until""",
        uid, username, full_name,
    )


async def db_set_lang(uid: int, lang: str) -> None:
    await q_exec("UPDATE users SET lang = $2 WHERE user_id = $1", uid, lang)


async def db_get_lang(uid: int) -> Optional[str]:
    return await q_val("SELECT lang FROM users WHERE user_id = $1", uid)


async def db_find_user(token: str):
    """Find a user by numeric id or @username (only users who started the bot are known)."""
    token = token.strip()
    if re.fullmatch(r"\d{1,15}", token):
        return await q_row("SELECT * FROM users WHERE user_id = $1", int(token))
    name = token.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", name):
        return None
    return await q_row("SELECT * FROM users WHERE lower(username) = lower($1)", name)


async def db_set_vip(uid: int, until: Optional[datetime]) -> None:
    await q_exec("UPDATE users SET vip_until = $2 WHERE user_id = $1", uid, until)


async def db_users_info(ids: list[int]) -> list:
    if not ids:
        return []
    return await q_fetch(
        "SELECT user_id, username, full_name FROM users WHERE user_id = ANY($1::bigint[])", ids
    )


# ---------------------------------------------------------------- admins
async def db_admin_ids() -> list[int]:
    return [r["user_id"] for r in await q_fetch("SELECT user_id FROM admins ORDER BY added_at")]


async def db_add_admin(uid: int) -> None:
    await q_exec("INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)


async def db_del_admin(uid: int) -> None:
    await q_exec("DELETE FROM admins WHERE user_id = $1", uid)


# ---------------------------------------------------------------- settings
_SETTINGS: dict[str, str] = {}


async def get_setting(key: str, default: str = "") -> str:
    if key not in _SETTINGS:
        value = await q_val("SELECT value FROM settings WHERE key = $1", key)
        _SETTINGS[key] = value or ""
    return _SETTINGS[key] or default


async def set_setting(key: str, value: str) -> None:
    if value:
        await q_exec(
            """INSERT INTO settings (key, value) VALUES ($1, $2)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            key, value,
        )
    else:
        await q_exec("DELETE FROM settings WHERE key = $1", key)
    _SETTINGS[key] = value


# ---------------------------------------------------------------- channels
_CHANNELS: dict[str, list] = {}


async def get_channels(kind: str) -> list:
    if kind not in _CHANNELS:
        _CHANNELS[kind] = await q_fetch("SELECT * FROM channels WHERE kind = $1 ORDER BY id", kind)
    return _CHANNELS[kind]


async def db_add_channel(kind, chat_id, title, username, link, lang) -> None:
    await q_exec(
        """INSERT INTO channels (kind, chat_id, title, username, invite_link, lang)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (kind, chat_id) DO UPDATE
           SET title = EXCLUDED.title, username = EXCLUDED.username,
               invite_link = EXCLUDED.invite_link, lang = EXCLUDED.lang""",
        kind, chat_id, title, username, link, lang,
    )
    _CHANNELS.clear()


async def db_get_channel(channel_id: int):
    return await q_row("SELECT * FROM channels WHERE id = $1", channel_id)


async def db_del_channel(channel_id: int) -> None:
    await q_exec("DELETE FROM channels WHERE id = $1", channel_id)
    _CHANNELS.clear()


# ---------------------------------------------------------------- animes
def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def db_create_anime(name: str, poster_id: str, poster_type: str, year: int, code: Optional[int] = None):
    """Create an anime. code=None -> next sequential code (1, 2, 3, ...), otherwise the custom code."""

    async def work(con: asyncpg.Connection):
        async with con.transaction():
            use_code = code
            if use_code is None:
                await con.fetchval("SELECT pg_advisory_xact_lock($1)", -1)
                row = await con.fetchrow("SELECT value FROM settings WHERE key = 'seq_counter'")
                n = int(row["value"]) if row else 0
                while True:
                    n += 1
                    if not await con.fetchval("SELECT 1 FROM animes WHERE code = $1 LIMIT 1", n):
                        break
                await con.execute(
                    """INSERT INTO settings (key, value) VALUES ('seq_counter', $1)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                    str(n),
                )
                use_code = n
            return await con.fetchrow(
                """INSERT INTO animes (code, name, poster_id, poster_type, year)
                   VALUES ($1, $2, $3, $4, $5) RETURNING *""",
                use_code, name, poster_id, poster_type, year,
            )

    return await _with_conn(work)


async def db_get_anime(uid: int):
    return await q_row("SELECT * FROM animes WHERE uid = $1", uid)


async def db_count_by_code(code: int) -> int:
    return await q_val("SELECT count(*) FROM animes WHERE code = $1", code)


async def db_delete_anime(uid: int) -> bool:
    return bool(await q_val("DELETE FROM animes WHERE uid = $1 RETURNING 1", uid))


async def db_set_genres(uid: int, genres: list[str]) -> None:
    await q_exec("UPDATE animes SET genres = $2 WHERE uid = $1", uid, genres)


async def db_inc_views(uid: int) -> None:
    try:
        await q_exec("UPDATE animes SET views = views + 1 WHERE uid = $1", uid)
    except Exception as e:  # never let statistics break the bot
        log.warning("views update failed: %s", e)


_ORDERS = {
    "old": "year ASC, name ASC",
    "new": "year DESC, created_at DESC",
    "top": "views DESC, name ASC",
    "name": "name ASC, year ASC",
    "genre": "name ASC, year ASC",
    "code": "year ASC, name ASC",
}


async def db_list(kind: str, arg: str, limit: int, offset: int) -> tuple[list, int]:
    """Paginated anime list. kind: old | new | top | name | genre | code."""
    where, params = "TRUE", []
    if kind == "name":
        where, params = "name ILIKE $1 ESCAPE '\\'", [_like(arg)]
    elif kind == "genre":
        where, params = "$1 = ANY(genres)", [arg]
    elif kind == "code":
        where, params = "code = $1", [int(arg)]
    n = len(params)
    total = await q_val(f"SELECT count(*) FROM animes WHERE {where}", *params)
    rows = await q_fetch(
        f"SELECT uid, code, name, year, views FROM animes WHERE {where} "
        f"ORDER BY {_ORDERS[kind]} LIMIT ${n + 1} OFFSET ${n + 2}",
        *params, limit, offset,
    )
    return rows, total


async def db_search_pick(text: str) -> list:
    """Admin search: by exact code and/or partial name."""
    code = int(text) if re.fullmatch(r"\d{1,12}", text) else None
    return await q_fetch(
        """SELECT uid, code, name, year FROM animes
           WHERE name ILIKE $1 ESCAPE '\\' OR code = $2
           ORDER BY code ASC, name ASC LIMIT $3""",
        _like(text), code, PICK_LIMIT + 1,
    )


# ---------------------------------------------------------------- episodes
async def db_ep_count(uid: int) -> int:
    return await q_val("SELECT count(*) FROM episodes WHERE anime_uid = $1", uid)


async def db_ep_page(uid: int, limit: int, offset: int) -> list[int]:
    rows = await q_fetch(
        "SELECT number FROM episodes WHERE anime_uid = $1 ORDER BY number LIMIT $2 OFFSET $3",
        uid, limit, offset,
    )
    return [r["number"] for r in rows]


async def db_ep_get(uid: int, num: int):
    """One episode with the anime name and its previous / next episode numbers."""
    return await q_row(
        """SELECT e.file_id, e.file_type, a.name,
                  (SELECT max(number) FROM episodes WHERE anime_uid = e.anime_uid AND number < e.number) AS prev_num,
                  (SELECT min(number) FROM episodes WHERE anime_uid = e.anime_uid AND number > e.number) AS next_num
           FROM episodes e JOIN animes a ON a.uid = e.anime_uid
           WHERE e.anime_uid = $1 AND e.number = $2""",
        uid, num,
    )


async def db_ep_exists(uid: int, num: int) -> bool:
    return bool(await q_val("SELECT 1 FROM episodes WHERE anime_uid = $1 AND number = $2", uid, num))


async def db_ep_last(uid: int) -> int:
    return await q_val("SELECT COALESCE(MAX(number), 0) FROM episodes WHERE anime_uid = $1", uid)


async def db_ep_ranges(uid: int) -> list[tuple[int, int]]:
    rows = await q_fetch(
        """SELECT min(number) AS lo, max(number) AS hi FROM (
               SELECT number, number - row_number() OVER (ORDER BY number) AS grp
               FROM episodes WHERE anime_uid = $1
           ) s GROUP BY grp ORDER BY lo""",
        uid,
    )
    return [(r["lo"], r["hi"]) for r in rows]


async def db_ep_add_seq(uid: int, items: list[tuple[str, str]]) -> list[int]:
    """Append episodes after the last one. Returns the assigned numbers."""

    async def work(con: asyncpg.Connection):
        async with con.transaction():
            await con.fetchval("SELECT pg_advisory_xact_lock($1)", uid)
            last = await con.fetchval(
                "SELECT COALESCE(MAX(number), 0) FROM episodes WHERE anime_uid = $1", uid
            )
            rows = [(uid, last + i + 1, fid, ftype) for i, (fid, ftype) in enumerate(items)]
            await con.executemany(
                "INSERT INTO episodes (anime_uid, number, file_id, file_type) VALUES ($1, $2, $3, $4)",
                rows,
            )
            return [r[1] for r in rows]

    return await _with_conn(work)


async def db_ep_upsert(uid: int, num: int, file_id: str, ftype: str) -> bool:
    """Insert or replace one episode. Returns True when it replaced an existing one."""
    inserted = await q_val(
        """INSERT INTO episodes (anime_uid, number, file_id, file_type) VALUES ($1, $2, $3, $4)
           ON CONFLICT (anime_uid, number) DO UPDATE
           SET file_id = EXCLUDED.file_id, file_type = EXCLUDED.file_type, created_at = now()
           RETURNING (xmax = 0)""",
        uid, num, file_id, ftype,
    )
    return not inserted


async def db_ep_delete(uid: int, num: int) -> bool:
    return bool(
        await q_val("DELETE FROM episodes WHERE anime_uid = $1 AND number = $2 RETURNING 1", uid, num)
    )

# ============================================================================
# 4) CORE HELPERS
# ============================================================================
def t(lang: Optional[str], key: str, **kw) -> str:
    """Translate a key. Every text of the bot goes through here."""
    table = TEXTS.get(lang or DEFAULT_LANG) or TEXTS[DEFAULT_LANG]
    text = table.get(key)
    if text is None:
        text = TEXTS[DEFAULT_LANG].get(key, key)
    kw.setdefault("sep", SEP)
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        log.error("Bad format for text key %r", key)
        return text


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def card(title: str, body: str = "") -> str:
    """Title line, thin separator, content."""
    return f"<b>{title}</b>\n{SEP}\n{body}" if body else f"<b>{title}</b>\n{SEP}"


def short(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fmt_ranges(ranges: list[tuple[int, int]], limit: int = 20) -> str:
    parts = [str(lo) if lo == hi else f"{lo}–{hi}" for lo, hi in ranges[:limit]]
    return ", ".join(parts) + (" …" if len(ranges) > limit else "")


def compress_numbers(nums: list[int]) -> str:
    """[1,2,3,5] -> '1–3, 5'."""
    nums = sorted(set(nums))
    ranges: list[tuple[int, int]] = []
    for n in nums:
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return fmt_ranges(ranges)


_TASKS: set[asyncio.Task] = set()


def _task_done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if not task.cancelled() and task.exception():
        log.error("Background task failed", exc_info=task.exception())


def spawn(coro: Awaitable) -> asyncio.Task:
    """Run a coroutine in the background and keep a strong reference to it."""
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_task_done)
    return task


# ---------------------------------------------------------------- roles & user cache
ADMIN_IDS: set[int] = set()


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def is_staff(uid: int) -> bool:
    return uid == OWNER_ID or uid in ADMIN_IDS


def staff_ids() -> list[int]:
    return list({OWNER_ID, *ADMIN_IDS})


class IsStaff(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and is_staff(user.id))


class IsOwner(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and is_owner(user.id))


@dataclass
class UserInfo:
    uid: int
    lang: Optional[str]
    vip_until: Optional[datetime]
    ts: float = field(default_factory=time.monotonic)

    @property
    def is_vip(self) -> bool:
        return bool(self.vip_until and self.vip_until > utcnow())


USERS: dict[int, UserInfo] = {}


async def get_user_info(tg_user) -> UserInfo:
    info = USERS.get(tg_user.id)
    if info and time.monotonic() - info.ts < USER_CACHE_TTL:
        return info
    row = await db_upsert_user(tg_user.id, tg_user.username, tg_user.full_name)
    info = UserInfo(row["user_id"], row["lang"], row["vip_until"])
    if len(USERS) > 50_000:
        USERS.clear()
    USERS[info.uid] = info
    return info


async def lang_of(uid: int) -> str:
    info = USERS.get(uid)
    if info and info.lang:
        return info.lang
    try:
        return await db_get_lang(uid) or DEFAULT_LANG
    except Exception:
        return DEFAULT_LANG


async def save_lang(uid: int, lang: str) -> None:
    await db_set_lang(uid, lang)
    USERS.pop(uid, None)


async def save_vip(uid: int, until: Optional[datetime]) -> None:
    await db_set_vip(uid, until)
    USERS.pop(uid, None)


def fmt_until(until: datetime) -> str:
    return "∞" if until.year >= 2900 else until.strftime("%d.%m.%Y")


# ---------------------------------------------------------------- safe Telegram calls
async def safe(call: Callable[[], Awaitable[Any]], default: Any = None, retries: int = 3) -> Any:
    """Await a Telegram API call; handle flood limits, blocked users and harmless errors."""
    for attempt in range(retries):
        try:
            return await call()
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 30) + 0.5)
        except TelegramForbiddenError:
            return default  # user blocked the bot / bot was removed
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                log.warning("BadRequest: %s", e)
            return default
        except TelegramNetworkError:
            await asyncio.sleep(1 + attempt)
        except TelegramAPIError as e:
            log.warning("Telegram API error: %s", e)
            return default
    return default


async def ack(cb: CallbackQuery, text: Optional[str] = None, alert: bool = False) -> None:
    """Always answer a callback query (stops the loading spinner)."""
    try:
        await cb.answer(text, show_alert=alert)
    except TelegramBadRequest:
        pass  # query too old
    except TelegramAPIError as e:
        log.debug("answer failed: %s", e)


async def render(event: Message | CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> Optional[Message]:
    """Show a screen: edit the callback's message when possible, otherwise send a fresh one."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message):
            if msg.text is not None:
                try:
                    return await msg.edit_text(text, reply_markup=markup)
                except TelegramBadRequest as e:
                    if "not modified" in str(e).lower():
                        return msg
                    log.debug("edit failed, sending new message: %s", e)
                except TelegramRetryAfter as e:
                    await asyncio.sleep(min(e.retry_after, 10) + 0.5)
                except TelegramAPIError:
                    pass
            await safe(msg.delete)  # media message or failed edit: replace it
            return await safe(lambda: msg.answer(text, reply_markup=markup))
        return await safe(lambda: BOT.send_message(event.from_user.id, text, reply_markup=markup))
    return await safe(lambda: event.answer(text, reply_markup=markup))


async def send(chat_id: int, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> Optional[Message]:
    return await safe(lambda: BOT.send_message(chat_id, text, reply_markup=markup))


async def send_sticker(chat_id: int, key: str) -> None:
    file_id = STICKERS.get(key, "")
    if file_id:
        await safe(lambda: BOT.send_sticker(chat_id, file_id), retries=2)


# ---------------------------------------------------------------- keyboards
def btn(text: str, cb: Any = None, url: Optional[str] = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    data = cb.pack() if isinstance(cb, CallbackData) else cb
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def chunk(buttons: list[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def pager(page: int, pages: int, make: Callable[[int], CallbackData]) -> list[InlineKeyboardButton]:
    """Previous / current / next row. Returns [] when there is only one page."""
    if pages <= 1:
        return []
    row = []
    row.append(btn("◀️", make(page - 1)) if page > 0 else btn("·", "noop"))
    row.append(btn(f"{page + 1}/{pages}", "noop"))
    row.append(btn("▶️", make(page + 1)) if page < pages - 1 else btn("·", "noop"))
    return row


def cancel_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([btn(t(lang, "btn_cancel"), Act(action="panel"))])


# ---------------------------------------------------------------- callback data
class Nav(CallbackData, prefix="n"):
    to: str


class LangCb(CallbackData, prefix="lg"):
    code: str


class Lst(CallbackData, prefix="l"):
    kind: str
    page: int = 0
    arg: str = ""


class Ani(CallbackData, prefix="a"):
    uid: int


class EpP(CallbackData, prefix="e"):
    uid: int
    page: int


class Play(CallbackData, prefix="p"):
    uid: int
    num: int


class Act(CallbackData, prefix="x"):
    """Generic action callback used by the admin and owner panels."""

    action: str
    a: int = 0
    b: int = 0
    s: str = ""


# ---------------------------------------------------------------- FSM states
class SearchS(StatesGroup):
    name = State()
    code = State()


class SupportS(StatesGroup):
    writing = State()


class ReplyS(StatesGroup):
    writing = State()


class Pick(StatesGroup):
    query = State()


class AddAnime(StatesGroup):
    custom_id = State()
    name = State()
    poster = State()
    year = State()


class AddEp(StatesGroup):
    seq = State()
    custom_num = State()
    custom_video = State()


class DelEp(StatesGroup):
    number = State()


class OwnerS(StatesGroup):
    channel = State()
    chlang = State()
    vip_user = State()
    text = State()
    admin_add = State()
    sticker = State()


# ---------------------------------------------------------------- the single post template
def deep_link(uid: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=an_{uid}"


def anime_text(lang: str, a, *, eps: Optional[int] = None, extra: Optional[str] = None, genres: bool = False) -> str:
    """Anime caption. The channel post template is: poster + name, year, code (+ optional extra line)."""
    lines = [t(lang, "card_year", year=a["year"]), t(lang, "card_code", code=a["code"])]
    if genres and a["genres"]:
        lines.append(t(lang, "card_genres", genres=", ".join(t(lang, f"g_{g}") for g in a["genres"] if g in GENRES)))
    if eps is not None:
        lines.append(t(lang, "card_eps", n=eps) if eps else t(lang, "card_no_eps"))
    if extra:
        lines.append(extra)
    return card(f"🎬 {esc(a['name'])}", "\n".join(lines))


def post_kb(lang: str, uid: int, *rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return kb([btn(t(lang, "btn_open_bot"), url=deep_link(uid))], *rows)


async def send_poster(chat_id: int, a, caption: str, markup: Optional[InlineKeyboardMarkup] = None) -> Message:
    if a["poster_type"] == "video":
        return await BOT.send_video(chat_id, a["poster_id"], caption=caption, reply_markup=markup)
    return await BOT.send_photo(chat_id, a["poster_id"], caption=caption, reply_markup=markup)


async def publish_post(a, extra_eps: Optional[list[int]] = None) -> list[tuple[str, bool, str]]:
    """Publish the anime post to every post channel. Returns [(channel title, ok, error)]."""
    results: list[tuple[str, bool, str]] = []
    for ch in await get_channels("post"):
        lang = ch["lang"]
        extra = t(lang, "post_new_eps", eps=compress_numbers(extra_eps)) if extra_eps else None
        caption = anime_text(lang, a, extra=extra)
        error = ""
        for attempt in range(3):
            try:
                await send_poster(ch["chat_id"], a, caption, post_kb(lang, a["uid"]))
                error = ""
                break
            except TelegramRetryAfter as e:
                error = "flood limit"
                await asyncio.sleep(min(e.retry_after, 30) + 0.5)
            except TelegramNetworkError as e:
                error = "network"
                await asyncio.sleep(1 + attempt)
            except TelegramAPIError as e:
                error = short(str(e).replace("Bad Request: ", ""), 80)
                break
        results.append((ch["title"], not error, error))
        await asyncio.sleep(0.1)
    return results


def post_report(lang: str, results: list[tuple[str, bool, str]]) -> str:
    if not results:
        return t(lang, "post_no_channels")
    ok = sum(1 for _, good, _ in results if good)
    lines = [t(lang, "post_sent", ok=ok, total=len(results))]
    lines += [f"• {esc(title)}: {esc(err)}" for title, good, err in results if not good]
    return "\n".join(lines)


# ---------------------------------------------------------------- forced subscription
SUB_OK: dict[int, float] = {}
_MEMBER_OK = (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


async def is_member(ch, user_id: int) -> bool:
    for attempt in range(2):
        try:
            m = await BOT.get_chat_member(ch["chat_id"], user_id)
            if m.status in _MEMBER_OK:
                return True
            if m.status == ChatMemberStatus.RESTRICTED:
                return bool(getattr(m, "is_member", True))
            return False
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 3) + 0.2)
        except TelegramNetworkError:
            await asyncio.sleep(0.5)
        except TelegramAPIError as e:
            # Channel deleted / bot removed: do not lock every user out because of it.
            log.warning("Subscription check failed for %s: %s", ch["title"], e)
            return True
    return True


async def missing_channels(user_id: int, force: bool = False) -> list:
    """Forced-subscription channels the user has NOT joined yet."""
    now = time.monotonic()
    if not force and SUB_OK.get(user_id, 0) > now:
        return []
    channels = await get_channels("sub")
    if not channels:
        return []
    flags = await asyncio.gather(*(is_member(ch, user_id) for ch in channels))
    missing = [ch for ch, ok in zip(channels, flags) if not ok]
    if not missing:
        if len(SUB_OK) > 100_000:
            SUB_OK.clear()
        SUB_OK[user_id] = now + SUB_CACHE_TTL
    else:
        SUB_OK.pop(user_id, None)
    return missing


def sub_prompt(lang: str, missing: list) -> tuple[str, InlineKeyboardMarkup]:
    rows = [[btn(f"➕ {short(ch['title'], 40)}", url=ch["invite_link"])] for ch in missing if ch["invite_link"]]
    rows.append([btn(t(lang, "btn_check"), Nav(to="subcheck"))])
    return t(lang, "sub_text"), kb(*rows)


# ---------------------------------------------------------------- uploaded-episode batching (sequential mode)
@dataclass
class EpSession:
    anime_uid: int
    anime_name: str
    chat_id: int
    status_mid: int
    lang: str
    buffer: list[tuple[int, str, str]] = field(default_factory=list)  # (message_id, file_id, type)
    added: list[int] = field(default_factory=list)
    gen: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


EP_SESSIONS: dict[int, EpSession] = {}


def ep_status_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([btn(t(lang, "btn_finish"), Act(action="epfinish"))])


async def ep_flush(admin_id: int) -> None:
    """Save buffered uploads in the order they were sent (sorted by message id)."""
    sess = EP_SESSIONS.get(admin_id)
    if not sess:
        return
    async with sess.lock:
        if not sess.buffer:
            return
        items = sorted(sess.buffer, key=lambda x: x[0])
        sess.buffer = []
        try:
            nums = await db_ep_add_seq(sess.anime_uid, [(fid, ftype) for _, fid, ftype in items])
            sess.added.extend(nums)
            text = t(
                sess.lang, "ep_seq_status",
                name=esc(sess.anime_name), n=len(sess.added), eps=compress_numbers(sess.added),
            )
        except Exception as e:
            log.error("Saving episodes failed: %s", e)
            text = t(sess.lang, "ep_seq_failed", name=esc(sess.anime_name))
        await safe(
            lambda: BOT.edit_message_text(
                text, chat_id=sess.chat_id, message_id=sess.status_mid, reply_markup=ep_status_kb(sess.lang)
            )
        )


async def _ep_delayed(admin_id: int, gen: int) -> None:
    await asyncio.sleep(BATCH_DEBOUNCE)
    sess = EP_SESSIONS.get(admin_id)
    if sess and sess.gen == gen:
        await ep_flush(admin_id)


def ep_queue(admin_id: int, message_id: int, file_id: str, ftype: str) -> bool:
    sess = EP_SESSIONS.get(admin_id)
    if not sess:
        return False
    sess.buffer.append((message_id, file_id, ftype))
    sess.gen += 1
    spawn(_ep_delayed(admin_id, sess.gen))
    return True


async def ep_drop(admin_id: int) -> Optional[EpSession]:
    """Flush whatever is pending, forget the session and return it."""
    await ep_flush(admin_id)
    return EP_SESSIONS.pop(admin_id, None)


async def reset_flow(state: FSMContext, uid: int) -> None:
    """Leave any multi-step flow cleanly."""
    await ep_drop(uid)
    await state.clear()

# ============================================================================
# 5) MIDDLEWARE
# ============================================================================
class ContextMiddleware(BaseMiddleware):
    """Loads the user (language, VIP), injects lang / uinfo / role and enforces forced subscription."""

    @staticmethod
    def _exempt(event: TelegramObject) -> bool:
        if isinstance(event, Message):
            return bool(event.text and event.text.startswith("/start"))
        if isinstance(event, CallbackQuery):
            data = event.data or ""
            return data.startswith("n:subcheck") or data.startswith("lg:")
        return False

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or user.is_bot:
            return await handler(event, data)
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None  # the bot only works in private chats
        info = await get_user_info(user)
        lang = info.lang or DEFAULT_LANG
        role = "owner" if is_owner(user.id) else "admin" if is_staff(user.id) else "user"
        data["uinfo"], data["lang"], data["role"] = info, lang, role

        if role == "user" and info.lang and not info.is_vip and not self._exempt(event):
            missing = await missing_channels(user.id)
            if missing:
                text, markup = sub_prompt(lang, missing)
                if isinstance(event, CallbackQuery):
                    await ack(event)
                    await render(event, text, markup)
                else:
                    await send(user.id, text, markup)
                return None
        return await handler(event, data)


# ============================================================================
# 6) USER HANDLERS
# ============================================================================
user_router = Router(name="user")
# Commands live in their own router that is registered FIRST, so /start, /admin, /owner and
# /cancel always work, even in the middle of a multi-step flow.
cmd_router = Router(name="commands")

LAST_GROUP: dict[int, str] = {}
SUPPORT_TIMES: dict[int, list[float]] = {}


def kb_home(lang: str) -> InlineKeyboardMarkup:
    return kb([btn(t(lang, "btn_home"), Nav(to="main"))])


def kb_main(lang: str) -> InlineKeyboardMarkup:
    return kb(
        [btn(t(lang, "btn_search"), Nav(to="search"))],
        [btn(t(lang, "btn_vip"), Nav(to="vip")), btn(t(lang, "btn_about"), Nav(to="about"))],
        [btn(t(lang, "btn_support"), Nav(to="support")), btn(t(lang, "btn_lang"), Nav(to="lang"))],
    )


def kb_search(lang: str) -> InlineKeyboardMarkup:
    return kb(
        [btn(t(lang, "btn_s_name"), Nav(to="s_name")), btn(t(lang, "btn_s_code"), Nav(to="s_code"))],
        [btn(t(lang, "btn_s_genre"), Nav(to="genres"))],
        [btn(t(lang, "btn_s_old"), Lst(kind="old")), btn(t(lang, "btn_s_top"), Lst(kind="top"))],
        [btn(t(lang, "btn_s_new"), Lst(kind="new"))],
        [btn(t(lang, "btn_back"), Nav(to="main"))],
    )


def kb_genres(lang: str) -> InlineKeyboardMarkup:
    buttons = [btn(t(lang, f"g_{g}"), Lst(kind="genre", arg=g)) for g in GENRES]
    return kb(*chunk(buttons, 2), [btn(t(lang, "btn_back"), Nav(to="search"))])


def kb_lang(back: bool) -> InlineKeyboardMarkup:
    rows = [[btn(t("uz", "btn_lang_uz"), LangCb(code="uz")), btn(t("ru", "btn_lang_ru"), LangCb(code="ru"))]]
    if back:
        rows.append([btn("⬅️", Nav(to="main"))])
    return kb(*rows)


def menu_text(lang: str, name: str = "", welcome: bool = False) -> str:
    return t(lang, "welcome" if welcome else "menu", name=esc(name))


async def open_home(chat_id: int, lang: str, name: str, state: FSMContext) -> None:
    """Show the main menu (plus the welcome sticker / a deep-linked anime when pending)."""
    data = await state.get_data()
    pending, welcome = data.get("pending") or "", bool(data.get("welcome"))
    await state.clear()
    if welcome:
        await send_sticker(chat_id, "welcome")
    await send(chat_id, menu_text(lang, name, welcome), kb_main(lang))
    if pending.startswith("an_") and pending[3:].isdigit():
        await present_anime(chat_id, lang, int(pending[3:]))


# ---------------------------------------------------------------- /start, language, subscription
@cmd_router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, lang: str, uinfo: UserInfo):
    user = message.from_user
    await reset_flow(state, user.id)
    payload = (command.args or "").strip()[:64]
    if not uinfo.lang:  # first visit: choose a language first
        await state.update_data(pending=payload, welcome=True)
        await message.answer(t(DEFAULT_LANG, "lang_choose"), reply_markup=kb_lang(False))
        return
    await state.update_data(pending=payload)
    if not is_staff(user.id) and not uinfo.is_vip:
        missing = await missing_channels(user.id)
        if missing:
            text, markup = sub_prompt(lang, missing)
            await message.answer(text, reply_markup=markup)
            return
    await open_home(user.id, lang, user.first_name, state)


@cmd_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, lang: str, role: str):
    if role != "user":
        await show_panel(message, state, lang)
        return
    await reset_flow(state, message.from_user.id)
    await message.answer(menu_text(lang, message.from_user.first_name), reply_markup=kb_main(lang))


@user_router.callback_query(LangCb.filter())
async def cb_lang(cb: CallbackQuery, callback_data: LangCb, state: FSMContext, uinfo: UserInfo):
    user = cb.from_user
    code = callback_data.code if callback_data.code in LANGS else DEFAULT_LANG
    first = not uinfo.lang
    await save_lang(user.id, code)
    await ack(cb, t(code, "lang_saved"))
    if not first:
        await state.clear()
        await render(cb, menu_text(code, user.first_name), kb_main(code))
        return
    await state.update_data(welcome=True)
    if isinstance(cb.message, Message):
        await safe(cb.message.delete)
    if not is_staff(user.id):
        missing = await missing_channels(user.id)
        if missing:
            text, markup = sub_prompt(code, missing)
            await send(user.id, text, markup)
            return
    await open_home(user.id, code, user.first_name, state)


@user_router.callback_query(Nav.filter(F.to == "subcheck"))
async def cb_subcheck(cb: CallbackQuery, state: FSMContext, lang: str):
    user = cb.from_user
    missing = await missing_channels(user.id, force=True)
    if missing:
        await ack(cb, t(lang, "sub_not_yet"), alert=True)
        text, markup = sub_prompt(lang, missing)
        await render(cb, text, markup)
        return
    await ack(cb, t(lang, "sub_ok"))
    if isinstance(cb.message, Message):
        await safe(cb.message.delete)
    await open_home(user.id, lang, user.first_name, state)


@user_router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await ack(cb)


# ---------------------------------------------------------------- navigation
@user_router.callback_query(Nav.filter())
async def cb_nav(cb: CallbackQuery, callback_data: Nav, state: FSMContext, lang: str, uinfo: UserInfo):
    await ack(cb)
    to = callback_data.to
    user = cb.from_user
    if to == "main":
        await state.clear()
        await render(cb, menu_text(lang, user.first_name), kb_main(lang))
    elif to == "search":
        await state.clear()
        await render(cb, t(lang, "search_menu"), kb_search(lang))
    elif to == "genres":
        await state.clear()
        await render(cb, t(lang, "genre_menu"), kb_genres(lang))
    elif to == "s_name":
        await state.set_state(SearchS.name)
        await render(cb, t(lang, "ask_name"), kb([btn(t(lang, "btn_back"), Nav(to="search"))]))
    elif to == "s_code":
        await state.set_state(SearchS.code)
        await render(cb, t(lang, "ask_code"), kb([btn(t(lang, "btn_back"), Nav(to="search"))]))
    elif to == "vip":
        await state.clear()
        text = await get_setting(f"vip_{lang}") or await get_setting(f"vip_{'ru' if lang == 'uz' else 'uz'}")
        body = text or t(lang, "vip_soon")
        if uinfo.is_vip:
            body += "\n\n" + t(lang, "vip_active", until=fmt_until(uinfo.vip_until))
        await render(cb, card(t(lang, "vip_title"), body), kb_home(lang))
    elif to == "about":
        await state.clear()
        body = await get_setting(f"about_{lang}") or t(lang, "about_default")
        await render(cb, card(t(lang, "about_title"), body), kb_home(lang))
    elif to == "support":
        await state.set_state(SupportS.writing)
        await render(cb, t(lang, "support_prompt"), kb_home(lang))
    elif to == "lang":
        await state.clear()
        await render(cb, t(lang, "lang_choose"), kb_lang(True))


# ---------------------------------------------------------------- lists
def list_title(lang: str, kind: str, arg: str) -> str:
    if kind == "genre":
        return t(lang, "list_genre", genre=t(lang, f"g_{arg}") if arg in GENRES else esc(arg))
    if kind == "name":
        return t(lang, "list_name", q=esc(arg))
    if kind == "code":
        return t(lang, "list_code", code=esc(arg))
    return t(lang, f"list_{kind}")


async def show_list(event: Message | CallbackQuery, lang: str, kind: str, arg: str, page: int) -> None:
    per = LIST_PAGE_SIZE
    rows, total = await db_list(kind, arg, per, max(page, 0) * per)
    pages = max(1, -(-total // per))
    if total and page >= pages:  # page vanished (anime deleted meanwhile)
        page = pages - 1
        rows, total = await db_list(kind, arg, per, page * per)
    page = max(0, page)
    back = btn(t(lang, "btn_back"), Nav(to="genres" if kind == "genre" else "search"))
    title = list_title(lang, kind, arg)
    chat_id = event.from_user.id

    if total == 0:
        await render(event, card(title, t(lang, "list_empty")), kb([back]))
        await send_sticker(chat_id, "empty")
        return

    buttons = []
    for r in rows:
        label = f"{r['name']} · {r['year']}" + (f" · 👁 {r['views']}" if kind == "top" else "")
        buttons.append([btn(short(label, 56), Ani(uid=r["uid"]))])
    make = (lambda p: Lst(kind=kind, page=p)) if kind == "name" else (lambda p: Lst(kind=kind, page=p, arg=arg))
    text = card(title, t(lang, "list_count", total=total))
    await render(event, text, kb(*buttons, pager(page, pages, make), [back]))


@user_router.callback_query(Lst.filter())
async def cb_list(cb: CallbackQuery, callback_data: Lst, state: FSMContext, lang: str):
    await ack(cb)
    kind, arg = callback_data.kind, callback_data.arg
    if kind not in ("old", "new", "top", "name", "genre", "code"):
        return
    if kind == "name":
        arg = (await state.get_data()).get("q", "")
        if not arg:
            await render(cb, t(lang, "search_menu"), kb_search(lang))
            return
    elif kind == "code" and not arg.isdigit():
        return
    await show_list(cb, lang, kind, arg, callback_data.page)


@user_router.message(SearchS.name, F.text)
async def msg_search_name(message: Message, state: FSMContext, lang: str):
    q = message.text.strip()[:60]
    if len(q) < 2:
        await message.answer(t(lang, "name_too_short"))
        return
    await state.update_data(q=q)
    await show_list(message, lang, "name", q, 0)


@user_router.message(SearchS.code, F.text)
async def msg_search_code(message: Message, lang: str):
    text = message.text.strip()
    if not re.fullmatch(r"\d{1,12}", text):
        await message.answer(t(lang, "bad_code"))
        await send_sticker(message.from_user.id, "error")
        return
    code = int(text)
    rows, total = await db_list("code", str(code), 2, 0)
    if total == 1:
        await present_anime(message.from_user.id, lang, rows[0]["uid"])
    else:  # nothing found, or several animes share the code: show them all
        await show_list(message, lang, "code", str(code), 0)


# ---------------------------------------------------------------- anime card & episodes
def ep_kb(lang: str, uid: int, nums: list[int], page: int, total: int) -> InlineKeyboardMarkup:
    pages = max(1, -(-total // EP_PAGE_SIZE))
    rows = chunk([btn(str(n), Play(uid=uid, num=n)) for n in nums], EP_PER_ROW)
    rows.append(pager(page, pages, lambda p: EpP(uid=uid, page=p)))
    rows.append([btn(t(lang, "btn_home"), Nav(to="main"))])
    return kb(*rows)


async def present_anime(chat_id: int, lang: str, uid: int) -> None:
    a = await db_get_anime(uid)
    if not a:
        await send(chat_id, t(lang, "anime_gone"), kb_home(lang))
        return
    total = await db_ep_count(uid)
    nums = await db_ep_page(uid, EP_PAGE_SIZE, 0)
    caption = anime_text(lang, a, eps=total, genres=True)
    markup = ep_kb(lang, uid, nums, 0, total)
    if await safe(lambda: send_poster(chat_id, a, caption, markup)) is None:
        await send(chat_id, caption, markup)  # poster file unavailable: fall back to text


@user_router.callback_query(Ani.filter())
async def cb_anime(cb: CallbackQuery, callback_data: Ani, lang: str):
    await ack(cb)
    await present_anime(cb.from_user.id, lang, callback_data.uid)


@user_router.callback_query(EpP.filter())
async def cb_ep_page(cb: CallbackQuery, callback_data: EpP, lang: str):
    await ack(cb)
    uid = callback_data.uid
    total = await db_ep_count(uid)
    pages = max(1, -(-total // EP_PAGE_SIZE))
    page = min(max(callback_data.page, 0), pages - 1)
    nums = await db_ep_page(uid, EP_PAGE_SIZE, page * EP_PAGE_SIZE)
    if isinstance(cb.message, Message):
        msg = cb.message
        await safe(lambda: msg.edit_reply_markup(reply_markup=ep_kb(lang, uid, nums, page, total)))


@user_router.callback_query(Play.filter())
async def cb_play(cb: CallbackQuery, callback_data: Play, lang: str):
    uid, num = callback_data.uid, callback_data.num
    ep = await db_ep_get(uid, num)
    if not ep:
        await ack(cb, t(lang, "ep_missing"), alert=True)
        return
    await ack(cb)
    nav = []
    if ep["prev_num"]:
        nav.append(btn(f"◀️ {ep['prev_num']}", Play(uid=uid, num=ep["prev_num"])))
    if ep["next_num"]:
        nav.append(btn(f"{ep['next_num']} ▶️", Play(uid=uid, num=ep["next_num"])))
    markup = kb(nav, [btn(t(lang, "btn_all_eps"), Ani(uid=uid))])
    caption = card(f"🎬 {esc(ep['name'])}", t(lang, "ep_caption", n=num))
    chat_id = cb.from_user.id
    method = BOT.send_document if ep["file_type"] == "document" else BOT.send_video
    sent = await safe(lambda: method(chat_id, ep["file_id"], caption=caption, reply_markup=markup))
    if sent is None:
        await send(chat_id, t(lang, "ep_unavailable"), kb_home(lang))
        await send_sticker(chat_id, "error")
        return
    spawn(db_inc_views(uid))


# ---------------------------------------------------------------- support (Murojaat)
def support_rate_ok(uid: int) -> bool:
    limit, window = SUPPORT_LIMIT
    now = time.monotonic()
    times = [x for x in SUPPORT_TIMES.get(uid, []) if now - x < window]
    ok = len(times) < limit
    if ok:
        times.append(now)
    SUPPORT_TIMES[uid] = times
    return ok


async def deliver_support(message: Message) -> int:
    """Forward a user's message to every admin / the owner. Returns how many received it."""
    user = message.from_user
    who = esc(user.full_name) + (f" (@{esc(user.username)})" if user.username else "")
    delivered = 0
    for sid in staff_ids():
        if sid <= 0:
            continue
        slang = await lang_of(sid)
        header = t(slang, "sup_header", who=who, uid=user.id)
        reply_kb = kb([btn(t(slang, "btn_reply"), Act(action="sreply", a=user.id))])
        if message.text:
            body = header + "\n\n" + esc(short(message.text, 3500))
            sent = await safe(lambda: BOT.send_message(sid, body, reply_markup=reply_kb))
        else:
            await safe(lambda: BOT.send_message(sid, header))
            sent = await safe(
                lambda: BOT.copy_message(sid, message.chat.id, message.message_id, reply_markup=reply_kb)
            )
        delivered += 1 if sent is not None else 0
    return delivered


@user_router.message(SupportS.writing)
async def msg_support(message: Message, lang: str):
    uid = message.from_user.id
    if not support_rate_ok(uid):
        await message.answer(t(lang, "support_rate"))
        return
    notify = True
    gid = message.media_group_id
    if gid:  # album: confirm only once
        if LAST_GROUP.get(uid) == gid:
            notify = False
        LAST_GROUP[uid] = gid
        if len(LAST_GROUP) > 5000:
            LAST_GROUP.clear()
    delivered = await deliver_support(message)
    if notify:
        await message.answer(t(lang, "support_sent" if delivered else "support_fail"), reply_markup=kb_home(lang))


# ---------------------------------------------------------------- fallbacks (must be registered last)
fallback_router = Router(name="fallback")


@fallback_router.message(StateFilter(None))
async def fb_message(message: Message, state: FSMContext, lang: str, uinfo: UserInfo):
    if message.text and message.text.startswith("/"):
        if message.text.split()[0].lower().split("@")[0] in ("/admin", "/owner"):
            await message.answer(t(lang, "no_access"))
            return
    if not uinfo.lang:
        await message.answer(t(DEFAULT_LANG, "lang_choose"), reply_markup=kb_lang(False))
        return
    await message.answer(menu_text(lang, message.from_user.first_name), reply_markup=kb_main(lang))


@fallback_router.message()
async def fb_state_message(message: Message, lang: str):
    await message.answer(t(lang, "unexpected_input"))


@fallback_router.callback_query()
async def fb_callback(cb: CallbackQuery, lang: str):
    await ack(cb, t(lang, "expired"))

# ============================================================================
# 7) ADMIN HANDLERS  (admins and the owner)
# ============================================================================
admin_router = Router(name="admin")
admin_router.message.filter(IsStaff())
admin_router.callback_query.filter(IsStaff())


def kb_admin(lang: str, owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [btn(t(lang, "a_add_anime"), Act(action="a_add")), btn(t(lang, "a_del_anime"), Act(action="a_del"))],
        [btn(t(lang, "a_add_ep"), Act(action="a_addep")), btn(t(lang, "a_del_ep"), Act(action="a_delep"))],
        [btn(t(lang, "a_post"), Act(action="a_post")), btn(t(lang, "a_auto"), Act(action="a_auto"))],
    ]
    if owner:
        rows.append([btn(t(lang, "btn_owner_panel"), Act(action="opanel"))])
    return kb(*rows)


def kb_panel(lang: str, new: bool = False) -> InlineKeyboardMarkup:
    return kb([btn(t(lang, "btn_panel"), Act(action="panelnew" if new else "panel"))])


async def show_panel(event: Message | CallbackQuery, state: FSMContext, lang: str) -> None:
    uid = event.from_user.id
    await reset_flow(state, uid)
    await render(event, card(t(lang, "admin_title"), t(lang, "admin_hint")), kb_admin(lang, is_owner(uid)))


@cmd_router.message(Command("admin"), IsStaff())
async def cmd_admin(message: Message, state: FSMContext, lang: str):
    await show_panel(message, state, lang)


@admin_router.callback_query(Act.filter(F.action == "panel"))
async def cb_panel(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await show_panel(cb, state, lang)


@admin_router.callback_query(Act.filter(F.action == "panelnew"))
async def cb_panel_new(cb: CallbackQuery, state: FSMContext, lang: str):
    """Open the panel as a NEW message (used under poster previews that must stay visible)."""
    await ack(cb)
    uid = cb.from_user.id
    await reset_flow(state, uid)
    await send(uid, card(t(lang, "admin_title"), t(lang, "admin_hint")), kb_admin(lang, is_owner(uid)))


# ---------------------------------------------------------------- picking an anime (shared by 4 functions)
async def start_pick(event: Message | CallbackQuery, state: FSMContext, lang: str, purpose: str) -> None:
    await reset_flow(state, event.from_user.id)
    await state.set_state(Pick.query)
    await state.update_data(purpose=purpose)
    await render(event, card(t(lang, f"pick_title_{purpose}"), t(lang, "pick_prompt")), cancel_kb(lang))


@admin_router.callback_query(Act.filter(F.action.in_({"a_addep", "a_delep", "a_del", "a_post"})))
async def cb_pick_entry(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    purpose = {"a_addep": "add_ep", "a_delep": "del_ep", "a_del": "del_anime", "a_post": "post"}[callback_data.action]
    if purpose == "post" and not await get_channels("post"):
        await render(cb, card(t(lang, "a_post"), t(lang, "post_no_channels")), kb_panel(lang))
        return
    await start_pick(cb, state, lang, purpose)


@admin_router.message(Pick.query, F.text)
async def msg_pick_query(message: Message, lang: str):
    text = message.text.strip()[:80]
    rows = await db_search_pick(text)
    if not rows:
        await message.answer(t(lang, "pick_none"), reply_markup=cancel_kb(lang))
        await send_sticker(message.from_user.id, "empty")
        return
    more = len(rows) > PICK_LIMIT
    buttons = [
        [btn(short(f"#{r['code']} · {r['name']} · {r['year']}", 56), Act(action="pick", a=r["uid"]))]
        for r in rows[:PICK_LIMIT]
    ]
    body = t(lang, "pick_more" if more else "pick_choose")
    await message.answer(card(t(lang, "pick_results"), body), reply_markup=kb(*buttons, [btn(t(lang, "btn_cancel"), Act(action="panel"))]))


@admin_router.callback_query(Act.filter(F.action == "pick"), Pick.query)
async def cb_pick(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    purpose = (await state.get_data()).get("purpose")
    a = await db_get_anime(callback_data.a)
    if not a:
        await render(cb, t(lang, "anime_gone"), kb_panel(lang))
        return
    uid = cb.from_user.id
    name = esc(a["name"])
    if purpose == "add_ep":
        await state.clear()
        total, last = await db_ep_count(a["uid"]), await db_ep_last(a["uid"])
        markup = kb(
            [
                btn(t(lang, "btn_mode_seq"), Act(action="epmode", a=a["uid"], s="seq")),
                btn(t(lang, "btn_mode_custom"), Act(action="epmode", a=a["uid"], s="custom")),
            ],
            [btn(t(lang, "btn_cancel"), Act(action="panel"))],
        )
        await render(cb, card(t(lang, "a_add_ep"), t(lang, "ep_mode_text", name=name, n=total, last=last)), markup)
    elif purpose == "del_ep":
        await begin_del_ep(cb, state, lang, a)
    elif purpose == "del_anime":
        await state.clear()
        total = await db_ep_count(a["uid"])
        markup = kb(
            [btn(t(lang, "btn_yes_delete"), Act(action="delok", a=a["uid"])), btn(t(lang, "btn_no"), Act(action="panel"))]
        )
        await render(cb, card(t(lang, "a_del_anime"), t(lang, "del_confirm", name=name, year=a["year"], code=a["code"], n=total)), markup)
    elif purpose == "post":
        await state.clear()
        if isinstance(cb.message, Message):
            await safe(cb.message.delete)
        markup = post_kb(
            lang, a["uid"],
            [btn(t(lang, "btn_post_send"), Act(action="postsend", a=a["uid"]))],
            [btn(t(lang, "btn_cancel"), Act(action="panel"))],
        )
        caption = anime_text(lang, a)
        if await safe(lambda: send_poster(uid, a, caption, markup)) is None:
            await send(uid, caption, markup)


# ---------------------------------------------------------------- 1) add anime
@admin_router.callback_query(Act.filter(F.action == "a_add"))
async def cb_add_anime(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    markup = kb(
        [
            btn(t(lang, "btn_id_seq"), Act(action="amode", s="seq")),
            btn(t(lang, "btn_id_custom"), Act(action="amode", s="custom")),
        ],
        [btn(t(lang, "btn_cancel"), Act(action="panel"))],
    )
    await render(cb, card(t(lang, "a_add_anime"), t(lang, "add_ask_mode")), markup)


@admin_router.callback_query(Act.filter(F.action == "amode"))
async def cb_add_mode(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    await state.clear()
    if callback_data.s == "custom":
        await state.set_state(AddAnime.custom_id)
        await render(cb, card(t(lang, "a_add_anime"), t(lang, "add_ask_id")), cancel_kb(lang))
    else:
        await state.set_state(AddAnime.name)
        await state.update_data(code=None)
        await render(cb, card(t(lang, "a_add_anime"), t(lang, "add_ask_name")), cancel_kb(lang))


@admin_router.message(AddAnime.custom_id, F.text)
async def msg_add_id(message: Message, state: FSMContext, lang: str):
    text = message.text.strip()
    if not re.fullmatch(r"\d{1,10}", text) or int(text) < 1:
        await message.answer(t(lang, "add_bad_id"), reply_markup=cancel_kb(lang))
        return
    await state.update_data(code=int(text))
    await state.set_state(AddAnime.name)
    await message.answer(card(t(lang, "a_add_anime"), t(lang, "add_ask_name")), reply_markup=cancel_kb(lang))


@admin_router.message(AddAnime.name, F.text)
async def msg_add_name(message: Message, state: FSMContext, lang: str):
    name = message.text.strip()
    if not 1 <= len(name) <= 200:
        await message.answer(t(lang, "add_bad_name"), reply_markup=cancel_kb(lang))
        return
    await state.update_data(name=name)
    await state.set_state(AddAnime.poster)
    await message.answer(card(t(lang, "a_add_anime"), t(lang, "add_ask_poster")), reply_markup=cancel_kb(lang))


@admin_router.message(AddAnime.poster, F.photo | F.video)
async def msg_add_poster(message: Message, state: FSMContext, lang: str):
    if message.photo:
        file_id, ptype = message.photo[-1].file_id, "photo"
    else:
        file_id, ptype = message.video.file_id, "video"
    await state.update_data(poster_id=file_id, poster_type=ptype)
    await state.set_state(AddAnime.year)
    await message.answer(card(t(lang, "a_add_anime"), t(lang, "add_ask_year")), reply_markup=cancel_kb(lang))


def anime_admin_kb(lang: str, uid: int) -> InlineKeyboardMarkup:
    return post_kb(
        lang, uid,
        [btn(t(lang, "btn_genres"), Act(action="gedit", a=uid))],
        [btn(t(lang, "btn_panel"), Act(action="panelnew"))],
    )


@admin_router.message(AddAnime.year, F.text)
async def msg_add_year(message: Message, state: FSMContext, lang: str):
    text = message.text.strip()
    if not re.fullmatch(r"\d{4}", text) or not 1900 <= int(text) <= utcnow().year + 2:
        await message.answer(t(lang, "add_bad_year"), reply_markup=cancel_kb(lang))
        return
    data = await state.get_data()
    await state.clear()
    chat_id = message.from_user.id
    custom = data.get("code") is not None
    try:
        a = await db_create_anime(data["name"], data["poster_id"], data["poster_type"], int(text), data.get("code"))
    except Exception as e:
        log.error("Creating anime failed: %s", e)
        await message.answer(t(lang, "err_generic"), reply_markup=kb_panel(lang))
        return
    shared = await db_count_by_code(a["code"]) if custom else 1
    note = t(lang, "add_saved", code=a["code"])
    if shared > 1:
        note += "\n" + t(lang, "add_shared", n=shared)
    await send_sticker(chat_id, "success")
    await send(chat_id, card(t(lang, "done_title"), note))
    caption, markup = anime_text(lang, a), anime_admin_kb(lang, a["uid"])
    if await safe(lambda: send_poster(chat_id, a, caption, markup)) is None:
        await send(chat_id, caption, markup)
    if await get_setting("auto_post") == "1":
        await send(chat_id, post_report(lang, await publish_post(a)))


# genre tagging right after creation (needed for "search by genre")
def kb_genre_edit(lang: str, uid: int, selected: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        btn(("✅ " if g in selected else "") + t(lang, f"g_{g}"), Act(action="gtog", a=uid, s=g)) for g in GENRES
    ]
    return kb(*chunk(buttons, 2), [btn(t(lang, "btn_done"), Act(action="gdone", a=uid))])


async def _edit_markup(cb: CallbackQuery, markup: InlineKeyboardMarkup) -> None:
    if isinstance(cb.message, Message):
        msg = cb.message
        await safe(lambda: msg.edit_reply_markup(reply_markup=markup))


@admin_router.callback_query(Act.filter(F.action == "gedit"))
async def cb_genre_edit(cb: CallbackQuery, callback_data: Act, lang: str):
    a = await db_get_anime(callback_data.a)
    if not a:
        await ack(cb, t(lang, "anime_gone"), alert=True)
        return
    await ack(cb, t(lang, "genre_hint"))
    await _edit_markup(cb, kb_genre_edit(lang, a["uid"], list(a["genres"])))


@admin_router.callback_query(Act.filter(F.action == "gtog"))
async def cb_genre_toggle(cb: CallbackQuery, callback_data: Act, lang: str):
    a = await db_get_anime(callback_data.a)
    if not a or callback_data.s not in GENRES:
        await ack(cb, t(lang, "anime_gone"), alert=True)
        return
    await ack(cb)
    genres = list(a["genres"])
    if callback_data.s in genres:
        genres.remove(callback_data.s)
    else:
        genres.append(callback_data.s)
    genres = [g for g in GENRES if g in genres]  # keep the canonical order
    await db_set_genres(a["uid"], genres)
    await _edit_markup(cb, kb_genre_edit(lang, a["uid"], genres))


@admin_router.callback_query(Act.filter(F.action == "gdone"))
async def cb_genre_done(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb, t(lang, "genre_saved"))
    await _edit_markup(cb, anime_admin_kb(lang, callback_data.a))


# ---------------------------------------------------------------- 2) delete anime
@admin_router.callback_query(Act.filter(F.action == "delok"))
async def cb_delete_anime(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb)
    deleted = await db_delete_anime(callback_data.a)
    await render(cb, card(t(lang, "a_del_anime"), t(lang, "deleted_anime" if deleted else "anime_gone")), kb_panel(lang))


# ---------------------------------------------------------------- 3) add episodes
@admin_router.callback_query(Act.filter(F.action == "epmode"))
async def cb_ep_mode(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    admin_id = cb.from_user.id
    a = await db_get_anime(callback_data.a)
    if not a:
        await render(cb, t(lang, "anime_gone"), kb_panel(lang))
        return
    await reset_flow(state, admin_id)
    name = esc(a["name"])
    if callback_data.s == "seq":
        last = await db_ep_last(a["uid"])
        msg = await render(cb, card(t(lang, "a_add_ep"), t(lang, "ep_seq_intro", name=name, next=last + 1)), ep_status_kb(lang))
        if msg is None:
            return
        await state.set_state(AddEp.seq)
        EP_SESSIONS[admin_id] = EpSession(
            anime_uid=a["uid"], anime_name=a["name"], chat_id=admin_id, status_mid=msg.message_id, lang=lang
        )
    else:
        ranges = await db_ep_ranges(a["uid"])
        await state.set_state(AddEp.custom_num)
        await state.update_data(uid=a["uid"], added=[])
        have = fmt_ranges(ranges) if ranges else "—"
        await render(cb, card(t(lang, "a_add_ep"), t(lang, "ep_custom_intro", name=name, have=have)), ep_status_kb(lang))


def _video_from(message: Message) -> Optional[tuple[str, str]]:
    if message.video:
        return message.video.file_id, "video"
    doc = message.document
    if doc and (doc.mime_type or "").startswith("video/"):
        return doc.file_id, "document"
    return None


@admin_router.message(AddEp.seq, F.video | F.document)
async def msg_ep_seq(message: Message, lang: str):
    media = _video_from(message)
    if not media:
        await message.answer(t(lang, "ep_need_video"))
        return
    if not ep_queue(message.from_user.id, message.message_id, media[0], media[1]):
        await message.answer(t(lang, "expired"), reply_markup=kb_panel(lang))


@admin_router.message(AddEp.custom_num, F.text)
async def msg_ep_custom_num(message: Message, state: FSMContext, lang: str):
    text = message.text.strip()
    if not re.fullmatch(r"\d{1,5}", text) or int(text) < 1:
        await message.answer(t(lang, "ep_bad_number"), reply_markup=ep_status_kb(lang))
        return
    num = int(text)
    uid = (await state.get_data())["uid"]
    exists = await db_ep_exists(uid, num)
    await state.update_data(num=num)
    await state.set_state(AddEp.custom_video)
    await message.answer(t(lang, "ep_custom_video_replace" if exists else "ep_custom_video", n=num), reply_markup=ep_status_kb(lang))


@admin_router.message(AddEp.custom_video, F.video | F.document)
async def msg_ep_custom_video(message: Message, state: FSMContext, lang: str):
    media = _video_from(message)
    if not media:
        await message.answer(t(lang, "ep_need_video"))
        return
    data = await state.get_data()
    uid, num = data["uid"], data["num"]
    try:
        replaced = await db_ep_upsert(uid, num, media[0], media[1])
    except asyncpg.ForeignKeyViolationError:
        await state.clear()
        await message.answer(t(lang, "anime_gone"), reply_markup=kb_panel(lang))
        return
    added = list(data.get("added", []))
    if num not in added:
        added.append(num)
    await state.update_data(added=added)
    await state.set_state(AddEp.custom_num)
    await message.answer(
        t(lang, "ep_custom_saved_replaced" if replaced else "ep_custom_saved", n=num), reply_markup=ep_status_kb(lang)
    )


@admin_router.callback_query(Act.filter(F.action == "epfinish"))
async def cb_ep_finish(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    admin_id = cb.from_user.id
    sess = await ep_drop(admin_id)  # saves anything still buffered
    data = await state.get_data()
    anime_uid, nums = (sess.anime_uid, sess.added) if sess else (data.get("uid"), data.get("added", []))
    await state.clear()
    if not nums or not anime_uid:
        await render(cb, card(t(lang, "a_add_ep"), t(lang, "ep_none_added")), kb_panel(lang))
        return
    text = t(lang, "ep_done", n=len(nums), eps=compress_numbers(nums))
    if await get_setting("auto_post") == "1":
        a = await db_get_anime(anime_uid)
        if a:
            text += "\n\n" + post_report(lang, await publish_post(a, nums))
    await render(cb, card(t(lang, "done_title"), text), kb_panel(lang))
    await send_sticker(admin_id, "success")


# ---------------------------------------------------------------- 4) delete episode
async def begin_del_ep(event: CallbackQuery, state: FSMContext, lang: str, a) -> None:
    ranges = await db_ep_ranges(a["uid"])
    if not ranges:
        await state.clear()
        await render(event, card(t(lang, "a_del_ep"), t(lang, "delep_none", name=esc(a["name"]))), kb_panel(lang))
        return
    await state.set_state(DelEp.number)
    await state.update_data(uid=a["uid"])
    await render(event, card(t(lang, "a_del_ep"), t(lang, "delep_prompt", name=esc(a["name"]), eps=fmt_ranges(ranges))), cancel_kb(lang))


@admin_router.message(DelEp.number, F.text)
async def msg_del_ep_number(message: Message, state: FSMContext, lang: str):
    text = message.text.strip()
    uid = (await state.get_data()).get("uid")
    if not uid or not re.fullmatch(r"\d{1,5}", text):
        await message.answer(t(lang, "ep_bad_number"), reply_markup=cancel_kb(lang))
        return
    num = int(text)
    if not await db_ep_exists(uid, num):
        await message.answer(t(lang, "delep_missing", n=num), reply_markup=cancel_kb(lang))
        return
    a = await db_get_anime(uid)
    if not a:
        await state.clear()
        await message.answer(t(lang, "anime_gone"), reply_markup=kb_panel(lang))
        return
    markup = kb(
        [btn(t(lang, "btn_yes_delete"), Act(action="delep_ok", a=uid, b=num)), btn(t(lang, "btn_no"), Act(action="panel"))]
    )
    await message.answer(card(t(lang, "a_del_ep"), t(lang, "delep_confirm", name=esc(a["name"]), n=num)), reply_markup=markup)


@admin_router.callback_query(Act.filter(F.action == "delep_ok"))
async def cb_del_ep_ok(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb)
    deleted = await db_ep_delete(callback_data.a, callback_data.b)
    text = t(lang, "delep_done", n=callback_data.b) if deleted else t(lang, "delep_missing", n=callback_data.b)
    markup = kb(
        [btn(t(lang, "btn_del_more"), Act(action="delep_more", a=callback_data.a))],
        [btn(t(lang, "btn_panel"), Act(action="panel"))],
    )
    await render(cb, card(t(lang, "a_del_ep"), text), markup)


@admin_router.callback_query(Act.filter(F.action == "delep_more"))
async def cb_del_ep_more(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    a = await db_get_anime(callback_data.a)
    if not a:
        await render(cb, t(lang, "anime_gone"), kb_panel(lang))
        return
    await begin_del_ep(cb, state, lang, a)


# ---------------------------------------------------------------- 5) manual post
@admin_router.callback_query(Act.filter(F.action == "postsend"))
async def cb_post_send(cb: CallbackQuery, callback_data: Act, lang: str):
    a = await db_get_anime(callback_data.a)
    if not a:
        await ack(cb, t(lang, "anime_gone"), alert=True)
        return
    await ack(cb, t(lang, "post_sending"))
    await _edit_markup(cb, kb([]))  # remove the buttons first: no accidental double post
    results = await publish_post(a)
    await send(cb.from_user.id, card(t(lang, "a_post"), post_report(lang, results)), kb_panel(lang))
    if results and all(ok for _, ok, _ in results):
        await send_sticker(cb.from_user.id, "success")


# ---------------------------------------------------------------- 6) automatic post
async def show_auto(event: CallbackQuery, lang: str) -> None:
    on = await get_setting("auto_post") == "1"
    channels = await get_channels("post")
    body = t(lang, "auto_on" if on else "auto_off") + "\n\n" + t(lang, "auto_info", n=len(channels))
    markup = kb(
        [btn(t(lang, "btn_turn_off" if on else "btn_turn_on"), Act(action="auto_toggle"))],
        [btn(t(lang, "btn_back"), Act(action="panel"))],
    )
    await render(event, card(t(lang, "a_auto"), body), markup)


@admin_router.callback_query(Act.filter(F.action == "a_auto"))
async def cb_auto(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    await show_auto(cb, lang)


@admin_router.callback_query(Act.filter(F.action == "auto_toggle"))
async def cb_auto_toggle(cb: CallbackQuery, lang: str):
    await ack(cb)
    on = await get_setting("auto_post") == "1"
    await set_setting("auto_post", "0" if on else "1")
    await show_auto(cb, lang)


# ---------------------------------------------------------------- replying to support messages
@admin_router.callback_query(Act.filter(F.action == "sreply"))
async def cb_support_reply(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    await state.set_state(ReplyS.writing)
    await state.update_data(target=callback_data.a)
    await send(cb.from_user.id, t(lang, "reply_prompt", uid=callback_data.a), cancel_kb(lang))


@admin_router.message(ReplyS.writing)
async def msg_support_reply(message: Message, state: FSMContext, lang: str):
    target = (await state.get_data()).get("target")
    if not target:
        await state.clear()
        return
    tl = await lang_of(target)
    again = kb([btn(t(tl, "btn_write_again"), Nav(to="support"))])
    header = card(t(tl, "reply_title"))
    if message.text and len(message.html_text) <= 3500:
        sent = await safe(lambda: BOT.send_message(target, header + "\n" + message.html_text, reply_markup=again))
    else:
        await safe(lambda: BOT.send_message(target, header))
        sent = await safe(lambda: BOT.copy_message(target, message.chat.id, message.message_id, reply_markup=again))
    await state.clear()
    await message.answer(t(lang, "reply_sent" if sent is not None else "reply_failed"), reply_markup=kb_panel(lang))

# ============================================================================
# 8) OWNER HANDLERS  (owner only)
# ============================================================================
owner_router = Router(name="owner")
owner_router.message.filter(IsOwner())
owner_router.callback_query.filter(IsOwner())

TEXT_KEYS = ("about_uz", "about_ru", "vip_uz", "vip_ru")
FLAGS = {"uz": "🇺🇿", "ru": "🇷🇺"}


def kb_owner(lang: str) -> InlineKeyboardMarkup:
    return kb(
        [btn(t(lang, "o_post_channels"), Act(action="o_ch", s="post")), btn(t(lang, "o_sub_channels"), Act(action="o_ch", s="sub"))],
        [btn(t(lang, "o_vip"), Act(action="o_vip")), btn(t(lang, "o_about"), Act(action="o_about"))],
        [btn(t(lang, "o_admins"), Act(action="o_admins")), btn(t(lang, "o_sticker"), Act(action="o_sticker"))],
        [btn(t(lang, "btn_admin_panel"), Act(action="panel"))],
    )


def kb_back_owner(lang: str) -> InlineKeyboardMarkup:
    return kb([btn(t(lang, "btn_back"), Act(action="opanel"))])


async def show_owner_panel(event: Message | CallbackQuery, state: FSMContext, lang: str) -> None:
    await reset_flow(state, event.from_user.id)
    await render(event, card(t(lang, "owner_title"), t(lang, "owner_hint")), kb_owner(lang))


@cmd_router.message(Command("owner"), IsOwner())
async def cmd_owner(message: Message, state: FSMContext, lang: str):
    await show_owner_panel(message, state, lang)


@owner_router.callback_query(Act.filter(F.action == "opanel"))
async def cb_owner_panel(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await show_owner_panel(cb, state, lang)


# ---------------------------------------------------------------- post / forced-subscription channels
def parse_chat_ref(text: str) -> Optional[int | str]:
    s = text.strip()
    if re.fullmatch(r"-?\d{5,20}", s):
        n = int(s)
        return n if n < 0 else int(f"-100{n}")
    m = re.search(r"(?:https?://)?t\.me/c/(\d+)", s)
    if m:
        return int(f"-100{m.group(1)}")
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,})", s) or re.fullmatch(r"@([A-Za-z][A-Za-z0-9_]{3,})", s)
    return f"@{m.group(1)}" if m else None


async def resolve_channel(message: Message, kind: str) -> tuple[Optional[dict], str]:
    """Turn the owner's message (forward / @username / id / link) into a verified channel. Returns (info, error_key)."""
    ref: Optional[int | str] = None
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        ref = origin.chat.id
    elif message.text:
        ref = parse_chat_ref(message.text)
    if ref is None:
        return None, "ch_err_ref"
    try:
        chat = await BOT.get_chat(ref)
    except TelegramAPIError:
        return None, "ch_err_notfound"
    if chat.type not in ("channel", "supergroup", "group"):
        return None, "ch_err_type"
    try:
        me = await BOT.get_chat_member(chat.id, BOT.id)
    except TelegramAPIError:
        return None, "ch_err_admin"
    if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return None, "ch_err_admin"
    if kind == "post" and chat.type == "channel" and me.status == ChatMemberStatus.ADMINISTRATOR:
        if not getattr(me, "can_post_messages", False):
            return None, "ch_err_post_right"
    link = f"https://t.me/{chat.username}" if chat.username else None
    if not link and kind == "sub":
        link = getattr(chat, "invite_link", None)
        if not link:
            try:
                link = (await BOT.create_chat_invite_link(chat.id, name="AniZone")).invite_link
            except TelegramAPIError:
                link = None
        if not link:
            return None, "ch_err_link"
    title = chat.title or str(chat.id)
    return {"chat_id": chat.id, "title": title, "username": chat.username, "link": link}, ""


async def show_channels(event: Message | CallbackQuery, lang: str, kind: str) -> None:
    channels = await get_channels(kind)
    title = t(lang, "o_post_channels" if kind == "post" else "o_sub_channels")
    body = t(lang, f"ch_hint_{kind}") + "\n\n" + (t(lang, "ch_count", n=len(channels)) if channels else t(lang, "ch_empty"))
    rows = []
    for ch in channels:
        label = f"🗑 {short(ch['title'], 36)}" + (f" · {ch['lang'].upper()}" if kind == "post" else "")
        rows.append([btn(label, Act(action="chdel", a=ch["id"], s=kind))])
    rows.append([btn(t(lang, "btn_add"), Act(action="chadd", s=kind))])
    rows.append([btn(t(lang, "btn_back"), Act(action="opanel"))])
    await render(event, card(title, body), kb(*rows))


@owner_router.callback_query(Act.filter(F.action == "o_ch"))
async def cb_channels(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    await show_channels(cb, lang, callback_data.s if callback_data.s in ("post", "sub") else "post")


@owner_router.callback_query(Act.filter(F.action == "chadd"))
async def cb_channel_add(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    kind = callback_data.s if callback_data.s in ("post", "sub") else "post"
    await state.set_state(OwnerS.channel)
    await state.update_data(kind=kind)
    markup = kb([btn(t(lang, "btn_cancel"), Act(action="o_ch", s=kind))])
    await render(cb, card(t(lang, "o_post_channels" if kind == "post" else "o_sub_channels"), t(lang, "ch_add_prompt")), markup)


@owner_router.message(OwnerS.channel)
async def msg_channel(message: Message, state: FSMContext, lang: str):
    kind = (await state.get_data()).get("kind", "post")
    info, err = await resolve_channel(message, kind)
    if not info:
        markup = kb([btn(t(lang, "btn_cancel"), Act(action="o_ch", s=kind))])
        await message.answer(t(lang, err), reply_markup=markup)
        return
    if kind == "sub":
        await db_add_channel("sub", info["chat_id"], info["title"], info["username"], info["link"], "uz")
        SUB_OK.clear()
        await state.clear()
        await message.answer(t(lang, "ch_added", title=esc(info["title"])))
        await show_channels(message, lang, "sub")
        return
    await state.update_data(ch=info)
    await state.set_state(OwnerS.chlang)
    markup = kb([btn(t("uz", "btn_lang_uz"), Act(action="chlang", s="uz")), btn(t("ru", "btn_lang_ru"), Act(action="chlang", s="ru"))])
    await message.answer(card(esc(info["title"]), t(lang, "ch_ask_lang")), reply_markup=markup)


@owner_router.callback_query(Act.filter(F.action == "chlang"), OwnerS.chlang)
async def cb_channel_lang(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    await ack(cb)
    info = (await state.get_data()).get("ch")
    await state.clear()
    if not info:
        await render(cb, t(lang, "expired"), kb_back_owner(lang))
        return
    post_lang = callback_data.s if callback_data.s in LANGS else DEFAULT_LANG
    await db_add_channel("post", info["chat_id"], info["title"], info["username"], info["link"], post_lang)
    await send(cb.from_user.id, t(lang, "ch_added", title=esc(info["title"])))
    await show_channels(cb, lang, "post")


@owner_router.callback_query(Act.filter(F.action == "chdel"))
async def cb_channel_delete(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb)
    ch = await db_get_channel(callback_data.a)
    kind = callback_data.s if callback_data.s in ("post", "sub") else "post"
    if not ch:
        await show_channels(cb, lang, kind)
        return
    markup = kb(
        [btn(t(lang, "btn_yes_delete"), Act(action="chdelok", a=ch["id"], s=kind)), btn(t(lang, "btn_no"), Act(action="o_ch", s=kind))]
    )
    await render(cb, card(esc(ch["title"]), t(lang, "ch_del_confirm")), markup)


@owner_router.callback_query(Act.filter(F.action == "chdelok"))
async def cb_channel_delete_ok(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb, t(lang, "ch_deleted"))
    await db_del_channel(callback_data.a)
    SUB_OK.clear()
    await show_channels(cb, lang, callback_data.s if callback_data.s in ("post", "sub") else "post")


# ---------------------------------------------------------------- VIP
@owner_router.callback_query(Act.filter(F.action == "o_vip"))
async def cb_o_vip(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    markup = kb(
        [btn(t(lang, "btn_vip_give"), Act(action="vipgive"))],
        [
            btn(f"{t(lang, 'btn_vip_text')} {FLAGS['uz']}", Act(action="settext", s="vip_uz")),
            btn(f"{t(lang, 'btn_vip_text')} {FLAGS['ru']}", Act(action="settext", s="vip_ru")),
        ],
        [btn(t(lang, "btn_back"), Act(action="opanel"))],
    )
    await render(cb, card(t(lang, "o_vip"), t(lang, "vip_owner_hint")), markup)


@owner_router.callback_query(Act.filter(F.action == "vipgive"))
async def cb_vip_give(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await state.set_state(OwnerS.vip_user)
    await render(cb, card(t(lang, "btn_vip_give"), t(lang, "vip_ask_user")), kb([btn(t(lang, "btn_cancel"), Act(action="o_vip"))]))


@owner_router.message(OwnerS.vip_user, F.text)
async def msg_vip_user(message: Message, state: FSMContext, lang: str):
    row = await db_find_user(message.text)
    if not row:
        await message.answer(t(lang, "vip_user_unknown"), reply_markup=kb([btn(t(lang, "btn_cancel"), Act(action="o_vip"))]))
        return
    await state.clear()
    uid = row["user_id"]
    who = esc(row["full_name"] or uid) + (f" (@{esc(row['username'])})" if row["username"] else "")
    until = row["vip_until"]
    current = t(lang, "vip_current", until=fmt_until(until)) if until and until > utcnow() else t(lang, "vip_current_none")
    markup = kb(
        [btn(t(lang, "btn_days", n=7), Act(action="vipdur", a=uid, b=7)), btn(t(lang, "btn_days", n=30), Act(action="vipdur", a=uid, b=30))],
        [btn(t(lang, "btn_days", n=90), Act(action="vipdur", a=uid, b=90)), btn(t(lang, "btn_days", n=365), Act(action="vipdur", a=uid, b=365))],
        [btn(t(lang, "btn_forever"), Act(action="vipdur", a=uid, b=-1)), btn(t(lang, "btn_vip_remove"), Act(action="vipdur", a=uid, b=0))],
        [btn(t(lang, "btn_cancel"), Act(action="o_vip"))],
    )
    await message.answer(card(t(lang, "btn_vip_give"), t(lang, "vip_choose", who=who, uid=uid) + "\n" + current), reply_markup=markup)


@owner_router.callback_query(Act.filter(F.action == "vipdur"))
async def cb_vip_duration(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb)
    uid, days = callback_data.a, callback_data.b
    row = await db_find_user(str(uid))
    if not row:
        await render(cb, t(lang, "vip_user_unknown"), kb_back_owner(lang))
        return
    now = utcnow()
    if days == 0:
        until = None
    elif days < 0:
        until = FOREVER
    else:
        current = row["vip_until"]
        until = (current if current and current > now else now) + timedelta(days=days)
    await save_vip(uid, until)
    ul = await lang_of(uid)
    if until:
        await send(uid, card(t(ul, "vip_title"), t(ul, "vip_received", until=fmt_until(until))), kb_home(ul))
        result = t(lang, "vip_given", until=fmt_until(until))
    else:
        await send(uid, card(t(ul, "vip_title"), t(ul, "vip_removed")), kb_home(ul))
        result = t(lang, "vip_removed_owner")
    await render(cb, card(t(lang, "o_vip"), result), kb([btn(t(lang, "btn_back"), Act(action="o_vip"))]))


# ---------------------------------------------------------------- editable texts (Bot haqida / VIP info)
@owner_router.callback_query(Act.filter(F.action == "o_about"))
async def cb_o_about(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    markup = kb(
        [btn(t("uz", "btn_lang_uz"), Act(action="settext", s="about_uz")), btn(t("ru", "btn_lang_ru"), Act(action="settext", s="about_ru"))],
        [btn(t(lang, "btn_back"), Act(action="opanel"))],
    )
    await render(cb, card(t(lang, "o_about"), t(lang, "about_owner_hint")), markup)


@owner_router.callback_query(Act.filter(F.action == "settext"))
async def cb_set_text(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    key = callback_data.s
    if key not in TEXT_KEYS:
        await ack(cb)
        return
    await ack(cb)
    current = await get_setting(key)
    await state.set_state(OwnerS.text)
    await state.update_data(key=key)
    back = Act(action="o_about" if key.startswith("about") else "o_vip")
    rows = [[btn(t(lang, "btn_clear"), Act(action="cleartext", s=key))]] if current else []
    rows.append([btn(t(lang, "btn_cancel"), back)])
    body = t(lang, "text_prompt", flag=FLAGS[key[-2:]], current=current or t(lang, "text_empty"))
    await render(cb, card(t(lang, "o_about" if key.startswith("about") else "btn_vip_text"), body), kb(*rows))


@owner_router.message(OwnerS.text, F.text)
async def msg_set_text(message: Message, state: FSMContext, lang: str):
    key = (await state.get_data()).get("key")
    if key not in TEXT_KEYS:
        await state.clear()
        return
    text = message.html_text.strip()
    if len(text) > 3000:
        await message.answer(t(lang, "text_too_long"))
        return
    await set_setting(key, text)
    await state.clear()
    back = Act(action="o_about" if key.startswith("about") else "o_vip")
    await message.answer(t(lang, "text_saved"), reply_markup=kb([btn(t(lang, "btn_back"), back)]))


@owner_router.callback_query(Act.filter(F.action == "cleartext"))
async def cb_clear_text(cb: CallbackQuery, callback_data: Act, state: FSMContext, lang: str):
    key = callback_data.s
    if key in TEXT_KEYS:
        await set_setting(key, "")
    await ack(cb, t(lang, "text_cleared"))
    await state.clear()
    back = Act(action="o_about" if key.startswith("about") else "o_vip")
    await render(cb, card(t(lang, "o_about" if key.startswith("about") else "btn_vip_text"), t(lang, "text_cleared")), kb([btn(t(lang, "btn_back"), back)]))


# ---------------------------------------------------------------- admins
async def set_staff_commands(uid: int) -> None:
    lang = await lang_of(uid)
    cmds = [BotCommand(command="start", description=t(lang, "cmd_start")), BotCommand(command="admin", description=t(lang, "cmd_admin"))]
    if is_owner(uid):
        cmds.append(BotCommand(command="owner", description=t(lang, "cmd_owner")))
    await safe(lambda: BOT.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid)), retries=2)


async def show_admins(event: Message | CallbackQuery, lang: str) -> None:
    ids = sorted(ADMIN_IDS)
    names = {r["user_id"]: r for r in await db_users_info(ids)}
    rows = []
    for i in ids:
        r = names.get(i)
        label = short(r["full_name"], 24) if r and r["full_name"] else "ID"
        rows.append([btn(f"🗑 {label} · {i}", Act(action="admdel", a=i))])
    rows.append([btn(t(lang, "btn_add"), Act(action="admadd"))])
    rows.append([btn(t(lang, "btn_back"), Act(action="opanel"))])
    body = t(lang, "adm_count", n=len(ids)) if ids else t(lang, "adm_empty")
    await render(event, card(t(lang, "o_admins"), body), kb(*rows))


@owner_router.callback_query(Act.filter(F.action == "o_admins"))
async def cb_admins(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    await show_admins(cb, lang)


@owner_router.callback_query(Act.filter(F.action == "admadd"))
async def cb_admin_add(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await state.set_state(OwnerS.admin_add)
    await render(cb, card(t(lang, "o_admins"), t(lang, "adm_ask")), kb([btn(t(lang, "btn_cancel"), Act(action="o_admins"))]))


@owner_router.message(OwnerS.admin_add, F.text)
async def msg_admin_add(message: Message, state: FSMContext, lang: str):
    text = message.text.strip()
    cancel = kb([btn(t(lang, "btn_cancel"), Act(action="o_admins"))])
    if re.fullmatch(r"\d{5,15}", text):
        uid = int(text)
    else:
        row = await db_find_user(text)
        if not row:
            await message.answer(t(lang, "adm_unknown"), reply_markup=cancel)
            return
        uid = row["user_id"]
    if is_owner(uid) or uid in ADMIN_IDS:
        await message.answer(t(lang, "adm_exists"), reply_markup=cancel)
        return
    await db_add_admin(uid)
    ADMIN_IDS.add(uid)
    await state.clear()
    await set_staff_commands(uid)
    await send(uid, card(t(await lang_of(uid), "adm_notice_title"), t(await lang_of(uid), "adm_notice")))
    await message.answer(t(lang, "adm_added", uid=uid))
    await show_admins(message, lang)


@owner_router.callback_query(Act.filter(F.action == "admdel"))
async def cb_admin_delete(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb)
    markup = kb(
        [btn(t(lang, "btn_yes_delete"), Act(action="admdelok", a=callback_data.a)), btn(t(lang, "btn_no"), Act(action="o_admins"))]
    )
    await render(cb, card(t(lang, "o_admins"), t(lang, "adm_del_confirm", uid=callback_data.a)), markup)


@owner_router.callback_query(Act.filter(F.action == "admdelok"))
async def cb_admin_delete_ok(cb: CallbackQuery, callback_data: Act, lang: str):
    await ack(cb, t(lang, "adm_deleted"))
    await db_del_admin(callback_data.a)
    ADMIN_IDS.discard(callback_data.a)
    await safe(lambda: BOT.delete_my_commands(scope=BotCommandScopeChat(chat_id=callback_data.a)), retries=2)
    await show_admins(cb, lang)


# ---------------------------------------------------------------- sticker file_id helper
@owner_router.callback_query(Act.filter(F.action == "o_sticker"))
async def cb_sticker_helper(cb: CallbackQuery, state: FSMContext, lang: str):
    await ack(cb)
    await reset_flow(state, cb.from_user.id)
    await state.set_state(OwnerS.sticker)
    await render(cb, card(t(lang, "o_sticker"), t(lang, "sticker_prompt")), kb_back_owner(lang))


@owner_router.message(OwnerS.sticker, F.sticker)
async def msg_sticker(message: Message, lang: str):
    await message.answer(t(lang, "sticker_id", fid=esc(message.sticker.file_id)))

# ============================================================================
# 9) ERROR HANDLER, KEEP-ALIVE WEB SERVER, ENTRY POINT
# ============================================================================
async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "not modified" in str(exc).lower():
        return True
    if isinstance(exc, TelegramForbiddenError):  # the user blocked the bot
        return True
    if isinstance(exc, TelegramRetryAfter):
        log.warning("Flood limit hit: retry after %ss", exc.retry_after)
        return True
    log.error("Unhandled error while processing an update", exc_info=exc)
    update = event.update
    user_id = None
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        await ack(update.callback_query)
    elif update.message and update.message.from_user:
        user_id = update.message.from_user.id
    if user_id:
        lang = await lang_of(user_id)
        await send(user_id, t(lang, "err_generic"))
        await send_sticker(user_id, "error")
    return True


async def health(_: web.Request) -> web.Response:
    return web.Response(text="AniZone is running")


async def start_web() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Keep-alive server listening on port %s", PORT)
    return runner


async def setup_commands() -> None:
    for lang, code in (("uz", None), ("ru", "ru")):
        cmds = [BotCommand(command="start", description=t(lang, "cmd_start"))]
        await safe(lambda: BOT.set_my_commands(cmds, scope=BotCommandScopeDefault(), language_code=code), retries=2)
    for uid in staff_ids():
        if uid > 0:
            await set_staff_commands(uid)


async def main() -> None:
    global BOT, BOT_USERNAME
    missing = [n for n, v in (("BOT_TOKEN", BOT_TOKEN), ("OWNER_ID", OWNER_ID), ("DATABASE_URL", DATABASE_URL)) if not v]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    # Texts must exist in both languages - fail fast instead of showing raw keys to users.
    diff = set(TEXTS["uz"]) ^ set(TEXTS["ru"])
    if diff:
        raise SystemExit(f"Translation keys missing in one language: {sorted(diff)}")

    runner = await start_web()  # open the port first so Render's health check passes quickly
    try:
        await init_db()
        ADMIN_IDS.update(await db_admin_ids())

        BOT = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
        BOT_USERNAME = (await BOT.get_me()).username or ""

        dp = Dispatcher(storage=MemoryStorage())
        dp.message.outer_middleware(ContextMiddleware())
        dp.callback_query.outer_middleware(ContextMiddleware())
        dp.errors.register(on_error)
        # Order matters: commands first, then admin / owner flows, then user flows, then fallbacks.
        dp.include_routers(cmd_router, admin_router, owner_router, user_router, fallback_router)

        await setup_commands()
        await BOT.delete_webhook(drop_pending_updates=False)
        log.info("AniZone started as @%s", BOT_USERNAME)
        await dp.start_polling(BOT, allowed_updates=dp.resolve_used_update_types())
    finally:
        for admin_id in list(EP_SESSIONS):
            try:
                await ep_drop(admin_id)
            except Exception as e:
                log.warning("Could not flush episodes on shutdown: %s", e)
        await close_db()
        if "BOT" in globals() and isinstance(BOT, Bot):
            await BOT.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("AniZone stopped")

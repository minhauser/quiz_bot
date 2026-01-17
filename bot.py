import asyncio
import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Set

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =======================
# CONFIGURATION
# =======================

BOT_TOKEN = "8396597732:AAF2pnbN88GnnXwuBxHdesL-WPKskStySrA"

DB_PATH = "quiz_bot.db"
WORDS_FILE = "words.json"

ADMIN_USERNAME = "Sunnatulla_Mamur_Korean"

LEVEL_BEGINNER = "초급"
LEVEL_INTERMEDIATE = "중급"
LEVEL_ADVANCED = "고급"

LEVEL_ORDER = [LEVEL_BEGINNER, LEVEL_INTERMEDIATE, LEVEL_ADVANCED]

# how many in-a-row are needed to change level
LEVEL_UP_CORRECT_STREAK = 10
LEVEL_DOWN_WRONG_STREAK = 3

# =======================
# WORD DATA
# =======================

# each entry: {
#   "id": int,
#   "korean": str,
#   "uzbek": str,
#   "english": str,
#   "options": [str, str, str],
#   "correct_index": int (0-based),
#   "level": LEVEL_*
# }
WORDS = []  # will be filled by _load_words()


def _load_words():
    global WORDS
    WORDS = []
    word_id = 1

    words_file_path = Path(WORDS_FILE)
    if not words_file_path.exists():
        raise FileNotFoundError(
            f"Words file '{WORDS_FILE}' not found. Please create it with words for each level."
        )

    with open(words_file_path, "r", encoding="utf-8") as f:
        words_data = json.load(f)

    for level in LEVEL_ORDER:
        if level not in words_data:
            logging.warning(f"No words found for level: {level}")
            continue

        level_words = words_data[level]
        if not level_words:
            logging.warning(f"Empty word list for level: {level}")
            continue

        # создаем список всех корейских слов этого уровня для генерации
        # неправильных вариантов
        all_korean_words = [w["korean"] for w in level_words]

        for word_data in level_words:
            korean = word_data["korean"]
            uzbek = word_data["uzbek"]
            english = word_data["english"]
            russian = word_data["russian"]

            # генерируем варианты ответов: правильный + 2 неправильных из того
            # же уровня
            wrong_candidates = [
                w for w in all_korean_words if w != korean
            ]
            random.shuffle(wrong_candidates)

            wrong1 = wrong_candidates[0] if wrong_candidates else korean
            wrong2 = wrong_candidates[1] if len(
                wrong_candidates) > 1 else korean

            options = [korean, wrong1, wrong2]
            random.shuffle(options)
            correct_index = options.index(korean)

            WORDS.append(
                {
                    "id": word_id,
                    "korean": korean,
                    "uzbek": uzbek,
                    "english": english,
                    "russian": russian,
                    "options": options,
                    "correct_index": correct_index,
                    "level": level,
                }
            )

            word_id += 1

    if not WORDS:
        raise RuntimeError(
            "No words loaded! Please add words to words.json file.")

    logging.info(
        f"Loaded {len(WORDS)} words: "
        f"{sum(1 for w in WORDS if w['level'] == LEVEL_BEGINNER)} 초급, "
        f"{sum(1 for w in WORDS if w['level'] == LEVEL_INTERMEDIATE)} 중급, "
        f"{sum(1 for w in WORDS if w['level'] == LEVEL_ADVANCED)} 고급"
    )


_load_words()

WORDS_BY_LEVEL = {
    level: [w for w in WORDS if w["level"] == level] for level in LEVEL_ORDER
}
WORDS_BY_ID = {w["id"]: w for w in WORDS}


# =======================
# KEYBOARDS
# =======================

# =======================
# KEYBOARDS
# =======================

MAIN_MENU_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🔠퀴즈"),
            KeyboardButton(text="📊랭킹"),
            KeyboardButton(text="🎁추천"),
        ]
    ],
    resize_keyboard=True,
)

# =======================
# DATABASE
# =======================


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                total_score INTEGER NOT NULL DEFAULT 0,
                current_level TEXT NOT NULL,
                correct_streak INTEGER NOT NULL DEFAULT 0,
                wrong_streak INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                word_id INTEGER NOT NULL,
                is_correct INTEGER NOT NULL,
                delta_score INTEGER NOT NULL,
                level TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        await db.commit()


async def get_or_create_user(
        user_id: int,
        username: str | None,
        first_name: str | None):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, current_level, total_score, correct_streak, wrong_streak "
            "FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        await cur.close()

        if row:
            return {
                "user_id": row[0],
                "current_level": row[1],
                "total_score": row[2],
                "correct_streak": row[3],
                "wrong_streak": row[4],
            }

        # default to beginner for new users
        await db.execute(
            """
            INSERT INTO users (
                user_id, username, first_name, total_score,
                current_level, correct_streak, wrong_streak,
                created_at, updated_at
            ) VALUES (?, ?, ?, 0, ?, 0, 0, ?, ?)
            """,
            (user_id, username, first_name, LEVEL_BEGINNER, now, now),
        )
        await db.commit()

        return {
            "user_id": user_id,
            "current_level": LEVEL_BEGINNER,
            "total_score": 0,
            "correct_streak": 0,
            "wrong_streak": 0,
        }


async def update_user_stats(
    user_id: int,
    total_score: int,
    current_level: str,
    correct_streak: int,
    wrong_streak: int,
):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users
            SET total_score = ?,
                current_level = ?,
                correct_streak = ?,
                wrong_streak = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (total_score, current_level, correct_streak, wrong_streak, now, user_id),
        )
        await db.commit()


async def log_answer(
    user_id: int,
    word_id: int,
    is_correct: bool,
    delta_score: int,
    level: str,
):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO answers (user_id, word_id, is_correct, delta_score, level, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, word_id, int(is_correct), delta_score, level, now),
        )
        await db.commit()


# =======================
# QUIZ / ADAPTIVE LOGIC
# =======================


def choose_word_for_level(level: str) -> dict:
    # choosing random word inside level keeps repetition low across sessions
    return random.choice(WORDS_BY_LEVEL[level])


def _pretty_korean_word(raw: str) -> str:
    # hide technical numeric suffixes like "안녕하세요 2" from the user
    parts = raw.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return raw


def build_question_text(word: dict) -> str:
    lines = [
        "알맞은 것을 고르십시오.",
        "",
        f"🇺🇿 {word['uzbek']} | 🇺🇸 {word['english']} | 🇷🇺 {word['russian']}",
        "",
    ]
    return "\n".join(lines)


def build_options_keyboard(word: dict) -> InlineKeyboardMarkup:
    buttons = []
    for idx, option in enumerate(word["options"]):
        callback_data = f"ans:{word['id']}:{idx}"
        buttons.append([InlineKeyboardButton(
            text=f"{idx+1}) {option}", callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_next_level_on_streak(
        level: str,
        correct_streak: int,
        wrong_streak: int) -> str:
    idx = LEVEL_ORDER.index(level)
    new_level = level

    if correct_streak >= LEVEL_UP_CORRECT_STREAK and idx < len(
            LEVEL_ORDER) - 1:
        new_level = LEVEL_ORDER[idx + 1]
    elif wrong_streak >= LEVEL_DOWN_WRONG_STREAK and idx > 0:
        new_level = LEVEL_ORDER[idx - 1]

    return new_level


async def send_quiz_question(message: Message, user_state: dict):
    level = user_state["current_level"]
    word = choose_word_for_level(level)
    text = build_question_text(word)
    kb = build_options_keyboard(word)

    await message.answer(text, reply_markup=kb)


# =======================
# RATING LOGIC
# =======================


async def get_all_time_top10():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT user_id, COALESCE(username, ''), COALESCE(first_name, ''), total_score
            FROM users
            ORDER BY total_score DESC, user_id ASC
            LIMIT 10
            """
        )
        rows = await cur.fetchall()
        await cur.close()
    return rows


async def get_today_top10():
    # today is determined in utc; for more precise local behavior this should
    # use timezones
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT a.user_id,
                   COALESCE(u.username, ''),
                   COALESCE(u.first_name, ''),
                   CASE
                       WHEN SUM(a.delta_score) < 0 THEN 0
                       ELSE SUM(a.delta_score)
                   END AS today_score
            FROM answers a
            JOIN users u ON u.user_id = a.user_id
            WHERE DATE(a.created_at) = DATE('now')
            GROUP BY a.user_id
            ORDER BY today_score DESC, a.user_id ASC
            LIMIT 10
            """
        )
        rows = await cur.fetchall()
        await cur.close()
    return rows


async def get_user_rank(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT total_score FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None

        total_score = row[0]

        cur = await db.execute(
            "SELECT COUNT(*) FROM users WHERE total_score > ?",
            (total_score,),
        )
        higher_count = (await cur.fetchone())[0]
        await cur.close()

        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]
        await cur.close()

        rank = higher_count + 1
        return rank, total_users, total_score


async def format_rating_text(user_id: int) -> str:
    all_time = await get_all_time_top10()
    today = await get_today_top10()
    user_rank_info = await get_user_rank(user_id)

    lines: list[str] = []

    lines.append("🏆 랭킹")
    lines.append("")
    lines.append("🔹 전체 TOP 10")
    if not all_time:
        lines.append("  (아직 사용자가 없습니다)")
    else:
        for idx, (uid, username, first_name,
                  score) in enumerate(all_time, start=1):
            name = username or first_name or str(uid)
            lines.append(f"  {idx}. {name} — {score}점")

    lines.append("")
    lines.append("🔸 오늘 TOP 10")
    if not today:
        lines.append("  (오늘 활동한 사용자가 없습니다)")
    else:
        for idx, (uid, username, first_name,
                  score) in enumerate(today, start=1):
            name = username or first_name or str(uid)
            lines.append(f"  {idx}. {name} — {score}점")

    lines.append("")
    if user_rank_info is None:
        lines.append("내 순위: 아직 랭킹 정보가 없습니다.")
    else:
        rank, total_users, total_score = user_rank_info
        lines.append(f"내 순위: {rank}위 / 총 {total_users}명")
        lines.append(f"내 총 점수: {total_score}점")

    return "\n".join(lines)


# =======================
# ADMIN FUNCTIONS
# =======================


def is_admin(username: str | None) -> bool:
    return username == ADMIN_USERNAME


async def get_bot_statistics():
    async with aiosqlite.connect(DB_PATH) as db:
        # total users
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]
        await cur.close()

        # active users today
        cur = await db.execute(
            """
            SELECT COUNT(DISTINCT user_id)
            FROM answers
            WHERE DATE(created_at) = DATE('now')
            """
        )
        active_today = (await cur.fetchone())[0]
        await cur.close()

        # total answers
        cur = await db.execute("SELECT COUNT(*) FROM answers")
        total_answers = (await cur.fetchone())[0]
        await cur.close()

        # correct answers percentage
        cur = await db.execute(
            "SELECT COUNT(*) FROM answers WHERE is_correct = 1"
        )
        correct_answers = (await cur.fetchone())[0]
        await cur.close()
        correct_percentage = (
            round((correct_answers / total_answers * 100), 2)
            if total_answers > 0
            else 0
        )

        # users by level
        cur = await db.execute(
            """
            SELECT current_level, COUNT(*) as count
            FROM users
            GROUP BY current_level
            """
        )
        level_stats = await cur.fetchall()
        await cur.close()

        # new users today
        cur = await db.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE DATE(created_at) = DATE('now')
            """
        )
        new_users_today = (await cur.fetchone())[0]
        await cur.close()

        # total score sum
        cur = await db.execute("SELECT SUM(total_score) FROM users")
        total_score_sum = (await cur.fetchone())[0] or 0
        await cur.close()

    return {
        "total_users": total_users,
        "active_today": active_today,
        "new_users_today": new_users_today,
        "total_answers": total_answers,
        "correct_answers": correct_answers,
        "correct_percentage": correct_percentage,
        "level_stats": level_stats,
        "total_score_sum": total_score_sum,
    }


async def format_statistics_text() -> str:
    stats = await get_bot_statistics()
    lines: list[str] = []

    lines.append("📊 통계")
    lines.append("")
    lines.append(f"👥 총 사용자: {stats['total_users']}명")
    lines.append(f"🆕 오늘 가입: {stats['new_users_today']}명")
    lines.append(f"✅ 오늘 활동: {stats['active_today']}명")
    lines.append("")
    lines.append(f"📝 총 답변: {stats['total_answers']}개")
    lines.append(
        f"✓ 정답률: {stats['correct_percentage']}% ({stats['correct_answers']}/{stats['total_answers']})"
    )
    lines.append("")
    lines.append("📈 레벨별 사용자:")
    level_map = {
        LEVEL_BEGINNER: "초급",
        LEVEL_INTERMEDIATE: "중급",
        LEVEL_ADVANCED: "고급",
    }
    for level, count in stats["level_stats"]:
        level_name = level_map.get(level, level)
        lines.append(f"  • {level_name}: {count}명")
    lines.append("")
    lines.append(f"🏆 총 점수 합계: {stats['total_score_sum']}점")

    return "\n".join(lines)


async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        await cur.close()
    return [row[0] for row in rows]


def build_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 통계 보기", callback_data="admin:stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 모든 사용자에게 메시지 보내기",
                    callback_data="admin:broadcast",
                )
            ],
        ]
    )


# =======================
# BOT HANDLERS
# =======================

dp = Dispatcher()

# состояние для рассылки: хранит user_id админов, которые находятся в
# режиме рассылки
broadcast_mode: Set[int] = set()
# хранит сообщения, ожидающие подтверждения: user_id -> broadcast_data
# broadcast_data = {
#     "text": str,
#     "content_type": str,
#     "message": Message
# }
pending_broadcasts: dict[int, dict] = {}


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )

    level_ko = user["current_level"]
    level_map_uz = {
        LEVEL_BEGINNER: "Boshlang‘ich",
        LEVEL_INTERMEDIATE: "O‘rta",
        LEVEL_ADVANCED: "Yuqori",
    }
    level_map_ru = {
        LEVEL_BEGINNER: "Начальный",
        LEVEL_INTERMEDIATE: "Средний",
        LEVEL_ADVANCED: "Продвинутый",
    }
    level_map_en = {
        LEVEL_BEGINNER: "Beginner",
        LEVEL_INTERMEDIATE: "Intermediate",
        LEVEL_ADVANCED: "Advanced",
    }

    level_uz = level_map_uz.get(level_ko, level_ko)
    level_ru = level_map_ru.get(level_ko, level_ko)
    level_en = level_map_en.get(level_ko, level_ko)
    score = user["total_score"]

    welcome = [
        "🇰🇷안녕하세요!",
        "",
        "이 봇은 적응형 한국어 단어 퀴즈 봇입니다.",
        "",
        f"현재 레벨: {level_ko}",
        f"총 점수: {score}",
        "",
        "아래 메뉴에서 기능을 선택하세요.",
        "",
        "🇺🇿Assalomu alaykum!",
        "",
        "Bu bot moslashuvchan koreys tili so‘z viktorinasi botidir.",
        "",
        f"Joriy daraja: {level_uz}",
        f"Umumiy ball: {score}",
        "",
        "Quyidagi menyudan kerakli funksiyani tanlang.",
        "",
        "🇷🇺 Здравствуйте!",
        "",
        "Этот бот — адаптивный квиз-бот для изучения корейских слов.",
        "",
        f"Текущий уровень: {level_ru}",
        f"Общий счёт: {score}",
        "",
        "Пожалуйста, выберите нужную функцию в меню ниже.",
        "",
        "🇺🇸 Hello!",
        "",
        "This bot is an adaptive Korean vocabulary quiz bot.",
        "",
        f"Current level: {level_en}",
        f"Total score: {score}",
        "",
        "Please select a feature from the menu below.",
    ]
    await message.answer("\n".join(welcome), reply_markup=MAIN_MENU_KB)


@dp.message(F.text == "🔠퀴즈")
async def handle_quiz(message: Message):
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )
    await send_quiz_question(message, user)


@dp.message(F.text == "📊랭킹")
async def handle_rating(message: Message):
    text = await format_rating_text(message.from_user.id)
    await message.answer(text, reply_markup=MAIN_MENU_KB)


@dp.message(F.text == "🎁추천")
async def handle_recommend(message: Message):
    text = (
        "📚 무료 자료실 (한국어 / 우즈베크어 자료)\n\n"
        "👉 https://t.me/SunnatullaMamur_Bot"
    )
    await message.answer(text, reply_markup=MAIN_MENU_KB)


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.username):
        await message.answer("❌ 권한이 없습니다.")
        return

    text = "🔐 관리자 패널\n\n아래 메뉴에서 기능을 선택하세요."
    kb = build_admin_keyboard()
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "admin:stats")
async def handle_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ 권한이 없습니다.", show_alert=True)
        return

    text = await format_statistics_text()
    kb = build_admin_keyboard()
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "admin:broadcast")
async def handle_admin_broadcast_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ 권한이 없습니다.", show_alert=True)
        return

    broadcast_mode.add(callback.from_user.id)
    text = (
        "📢 모든 사용자에게 메시지 보내기\n\n"
        "보낼 메시지를 입력하세요.\n\n"
        "💡 팁: 메시지에 텍스트, эмодзи, ссылки 등을 포함할 수 있습니다.\n\n"
        "취소하려면 /cancel을 입력하세요."
    )
    await callback.message.edit_text(text)
    await callback.answer()


@dp.callback_query(F.data.startswith("admin:broadcast_confirm:"))
async def handle_admin_broadcast_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ 권한이 없습니다.", show_alert=True)
        return

    action = callback.data.split(":")[-1]

    if action == "yes":
        broadcast_data = pending_broadcasts.get(callback.from_user.id)
        if not broadcast_data:
            await callback.answer("❌ 메시지를 찾을 수 없습니다.", show_alert=True)
            broadcast_mode.discard(callback.from_user.id)
            pending_broadcasts.pop(callback.from_user.id, None)
            return

        await callback.message.edit_text("⏳ 메시지를 보내는 중...")
        await callback.answer()

        await send_broadcast(callback.bot, callback.from_user.id, broadcast_data)
        broadcast_mode.discard(callback.from_user.id)
        pending_broadcasts.pop(callback.from_user.id, None)
    else:
        broadcast_mode.discard(callback.from_user.id)
        pending_broadcasts.pop(callback.from_user.id, None)
        text = "❌ 취소되었습니다."
        kb = build_admin_keyboard()
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer("취소되었습니다.")


async def send_broadcast(bot: Bot, admin_id: int, broadcast_data: dict):
    user_ids = await get_all_user_ids()
    total = len(user_ids)
    success = 0
    failed = 0

    status_message = await bot.send_message(
        admin_id,
        f"📤 메시지 전송 중...\n\n전체: {total}명\n성공: {success}명\n실패: {failed}명"
    )

    message_text = broadcast_data.get("text", "")
    content_type = broadcast_data.get("content_type", "text")
    original_message = broadcast_data.get("message")

    for idx, user_id in enumerate(user_ids, 1):
        try:
            if content_type == "text":
                await bot.send_message(user_id, message_text)
            elif content_type == "photo" and original_message:
                await bot.send_photo(
                    user_id,
                    photo=original_message.photo[-1].file_id,
                    caption=message_text if message_text else None
                )
            elif content_type == "video" and original_message:
                await bot.send_video(
                    user_id,
                    video=original_message.video.file_id,
                    caption=message_text if message_text else None
                )
            elif content_type == "document" and original_message:
                await bot.send_document(
                    user_id,
                    document=original_message.document.file_id,
                    caption=message_text if message_text else None
                )
            elif content_type == "audio" and original_message:
                await bot.send_audio(
                    user_id,
                    audio=original_message.audio.file_id,
                    caption=message_text if message_text else None
                )
            elif content_type == "voice" and original_message:
                await bot.send_voice(
                    user_id,
                    voice=original_message.voice.file_id,
                    caption=message_text if message_text else None
                )
            else:
                # fallback to text
                if message_text:
                    await bot.send_message(user_id, message_text)

            success += 1
        except Exception as e:
            failed += 1
            error_msg = str(e)
            # не логируем ошибки блокировки бота пользователем как
            # предупреждения
            if "blocked" not in error_msg.lower() and "chat not found" not in error_msg.lower():
                logging.warning(f"Failed to send message to {user_id}: {e}")

        # обновляем статус каждые 10 сообщений или в конце
        if idx % 10 == 0 or idx == total:
            try:
                await status_message.edit_text(
                    f"📤 메시지 전송 중...\n\n"
                    f"전체: {total}명\n"
                    f"성공: {success}명\n"
                    f"실패: {failed}명\n"
                    f"진행률: {idx}/{total} ({round(idx/total*100, 1)}%)"
                )
            except BaseException:
                pass

        # небольшая задержка, чтобы не превысить лимиты API
        if idx % 30 == 0:
            await asyncio.sleep(1)

    # финальное сообщение
    final_text = (
        f"✅ 메시지 전송 완료!\n\n"
        f"📊 통계:\n"
        f"• 전체: {total}명\n"
        f"• 성공: {success}명\n"
        f"• 실패: {failed}명\n"
        f"• 성공률: {round(success/total*100, 1) if total > 0 else 0}%"
    )
    kb = build_admin_keyboard()

    try:
        await status_message.edit_text(final_text, reply_markup=kb)
    except BaseException:
        await bot.send_message(admin_id, final_text, reply_markup=kb)


@dp.callback_query(F.data.startswith("ans:"))
async def handle_answer(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("잘못된 응답입니다.", show_alert=True)
        return

    try:
        word_id = int(parts[1])
        selected_index = int(parts[2])
    except ValueError:
        await callback.answer("잘못된 응답입니다.", show_alert=True)
        return

    word = WORDS_BY_ID.get(word_id)
    if not word:
        await callback.answer("이 문항은 더 이상 유효하지 않습니다.", show_alert=True)
        return

    user = await get_or_create_user(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
    )

    correct_index = word["correct_index"]
    is_correct = selected_index == correct_index
    delta_score = 1 if is_correct else -1

    # do not allow total score to go below 0
    total_score = user["total_score"] + delta_score
    if total_score < 0:
        total_score = 0
    correct_streak = user["correct_streak"]
    wrong_streak = user["wrong_streak"]

    # streak updates must happen before deciding level change
    if is_correct:
        correct_streak += 1
        wrong_streak = 0
    else:
        wrong_streak += 1
        correct_streak = 0

    current_level = user["current_level"]
    new_level = get_next_level_on_streak(
        current_level, correct_streak, wrong_streak)

    level_changed = new_level != current_level
    level_change_message = None

    if level_changed:
        if LEVEL_ORDER.index(new_level) > LEVEL_ORDER.index(current_level):
            level_change_message = f"🎉 수준 상승! {current_level} → {new_level}"
        else:
            level_change_message = f"📉 수준 하락. {current_level} → {new_level}"
        # once level changes, streaks restart to avoid immediate multiple jumps
        correct_streak = 0
        wrong_streak = 0
        current_level = new_level

    await log_answer(
        user_id=user["user_id"],
        word_id=word_id,
        is_correct=is_correct,
        delta_score=delta_score,
        level=current_level,
    )
    await update_user_stats(
        user_id=user["user_id"],
        total_score=total_score,
        current_level=current_level,
        correct_streak=correct_streak,
        wrong_streak=wrong_streak,
    )

    if is_correct:
        feedback = "✅ 정답입니다!"
    else:
        correct_option_text = word["options"][correct_index]
        feedback = (
            "❌ 틀렸습니다.\n\n"
            f"정답: {correct_index+1}) {correct_option_text}"
        )

    feedback += f"\n\n현재 점수: {total_score}"

    if level_changed and level_change_message:
        feedback += f"\n\n{level_change_message}"

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(feedback, reply_markup=MAIN_MENU_KB)
    await callback.answer()

    # immediately send next question so learning stays continuous
    user_state = {
        "user_id": user["user_id"],
        "current_level": current_level,
        "total_score": total_score,
        "correct_streak": correct_streak,
        "wrong_streak": wrong_streak,
    }
    await send_quiz_question(callback.message, user_state)


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if message.from_user.id in broadcast_mode:
        broadcast_mode.discard(message.from_user.id)
        pending_broadcasts.pop(message.from_user.id, None)
        text = "❌ 메시지 전송이 취소되었습니다."
        kb = build_admin_keyboard()
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer("취소할 작업이 없습니다.")


@dp.message(F.from_user.id.in_(broadcast_mode))
async def handle_broadcast_message(message: Message):
    if not is_admin(message.from_user.username):
        broadcast_mode.discard(message.from_user.id)
        await message.answer("❌ 권한이 없습니다.")
        return

    message_text = message.text or message.caption or ""

    if not message_text.strip() and not (
            message.photo or message.video or message.document or message.audio or message.voice):
        await message.answer(
            "❌ 메시지가 비어있습니다. 텍스트 또는 미디어 메시지를 입력하세요.\n"
            "취소하려면 /cancel을 입력하세요."
        )
        return

    # определяем тип контента
    content_type = "text"
    media_info = ""

    if message.photo:
        content_type = "photo"
        media_info = "📷 사진"
    elif message.video:
        content_type = "video"
        media_info = "🎥 비디오"
    elif message.document:
        content_type = "document"
        media_info = f"📄 문서: {message.document.file_name or '이름 없음'}"
    elif message.audio:
        content_type = "audio"
        media_info = "🎵 오디오"
    elif message.voice:
        content_type = "voice"
        media_info = "🎤 음성 메시지"

    # показываем превью и запрашиваем подтверждение
    preview_text = f"📝 미리보기:\n\n"

    if content_type != "text":
        preview_text += f"{media_info}\n"
        if message_text:
            preview_text += f"\n{message_text}\n"
    else:
        preview_text += f"{message_text}\n"

    preview_text += (
        f"\n이 메시지를 모든 사용자에게 보내시겠습니까?\n\n"
        f"⚠️ 주의: 이 작업은 취소할 수 없습니다."
    )

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ 전송", callback_data=f"admin:broadcast_confirm:yes"
                ),
                InlineKeyboardButton(
                    text="❌ 취소", callback_data=f"admin:broadcast_confirm:no"
                ),
            ]
        ]
    )

    # сохраняем информацию о сообщении для рассылки
    broadcast_data = {
        "text": message_text,
        "content_type": content_type,
        "message": message,  # сохраняем объект сообщения для копирования медиа
    }
    pending_broadcasts[message.from_user.id] = broadcast_data

    await message.answer(preview_text, reply_markup=confirm_kb)


@dp.message()
async def fallback_message(message: Message):
    # проверяем, не находится ли пользователь в режиме рассылки
    if message.from_user.id in broadcast_mode:
        await handle_broadcast_message(message)
        return

    text = (
        "아래 메뉴에서 기능을 선택하세요:\n\n"
        "- 🔠퀴즈\n"
        "- 📊랭킹\n"
        "- 🎁추천"
    )
    await message.answer(text, reply_markup=MAIN_MENU_KB)


# =======================
# ENTRY POINT
# =======================


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Please set BOT_TOKEN to your Telegram bot token.")

    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

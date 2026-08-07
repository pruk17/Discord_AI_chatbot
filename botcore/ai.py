"""OpenRouter client and the ask_model() call (with multi-model fallback)."""

import datetime
import logging

from openai import AsyncOpenAI, APIStatusError

from . import config, memory

log = logging.getLogger(config.LOGGER_NAME)

# Async OpenAI-compatible client pointed at OpenRouter. max_retries=0 so we can
# fall back to the next model ourselves instead of retrying the same busy one.
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=config.OPENROUTER_API_KEY,
    max_retries=0,
)


async def ask_model(user_text: str, author: str, guild) -> tuple[str, str | None]:
    """Send system prompt + facts + roster + history + message; fall back on errors.

    Returns (visible_text, japanese_to_speak). The text is in the user's language;
    the Japanese part is present only when the bot is in a voice channel.
    """
    now = datetime.datetime.now(config.LOCAL_TZ)
    time_note = (
        f"\n\nThe current date and time is {now:%Y-%m-%d %H:%M} ({now:%A}). "
        "This is the real current time — use it whenever asked about the date or time."
    )
    # Only attach the (potentially large) roster when the question seems to need it.
    roster = memory.server_roster(guild) if memory.needs_roster(user_text) else ""
    # Ask for a Japanese spoken version only when we're actually going to speak.
    in_voice = config.TTS_ENABLED and guild is not None and guild.voice_client is not None
    say_note = config.SAY_NOTE if in_voice else ""
    system_content = (
        config.SYSTEM_PROMPT + config.MEMORY_NOTE + say_note + time_note
        + roster + memory.facts_block()
    )
    messages = [{"role": "system", "content": system_content}]
    messages.extend(memory.format_for_model(e) for e in memory.shared_history)
    messages.append({"role": "user", "content": f"{author}: {user_text}"})

    last_error: Exception | None = None
    for model in config.MODELS:
        try:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=config.MAX_TOKENS,
                temperature=config.TEMPERATURE,
                messages=messages,
            )
        except APIStatusError as exc:
            log.warning("Model %s unavailable (%s), trying next...", model, exc.status_code)
            last_error = exc
            continue

        raw = (response.choices[0].message.content or "").strip()
        reply = memory.apply_and_strip_facts(raw)
        reply, speak_text = memory.extract_speech(reply)
        if not reply:
            reply = "..."

        memory.remember_turn(user_text, author, reply)

        usage = response.usage
        if usage:
            log.info("Reply via %s: in=%s out=%s tokens",
                     model, usage.prompt_tokens, usage.completion_tokens)
        return reply, speak_text

    raise last_error if last_error else RuntimeError("No models configured")

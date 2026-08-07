"""botcore — modular pieces of the Discord AI chatbot.

Each module has one responsibility so the bot is easy to extend:
    config   — settings loaded from .env
    memory   — SQLite chat history + facts + server roster
    ai       — OpenRouter client and the ask_model() call
    sounds   — playing audio in voice channels
    dcutils  — small Discord message helpers
    slash    — all the slash (/) commands
The entry point (bot.py) wires these together.
"""

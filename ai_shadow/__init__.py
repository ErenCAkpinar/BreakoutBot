"""AI shadow veto (DEFTER Tur 15): record-only review of every FULL OPEN.

Reads the bot's state file and public Binance candles, asks a pinned model for
ALLOW / VETO_RECOMMENDED, and appends the answer to its own log. It never
imports trading code, never writes to the bot's directory and the bot never
waits for it.
"""

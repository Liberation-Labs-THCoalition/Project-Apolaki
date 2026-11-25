"""The demo's guards: who may ask, how much it may spend. Pure and clock-injectable, so tests need no network.

Every limit is checked BEFORE a model call, against the worst case that call could cost, so a day cannot overshoot its
cap by more than the estimate's error. The prices are assumptions (set them to the real ones); they default high on
purpose. The real hard stop is the spending limit set in the Anthropic console, not this file."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    max_drafts_per_day: int = 150
    max_usd_per_day: float = 3.00
    per_ip_per_hour: int = 5
    usd_per_mtok_in: float = 3.0            # assumed, deliberately high
    usd_per_mtok_out: float = 15.0          # assumed, deliberately high
    brief_min: int = 10
    brief_max: int = 600
    worst_case_input_tokens: int = 6000     # system prompt + brand + brief, per model call
    calls_per_draft: int = 2                # one draft, and at most one lint revision

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(max_drafts_per_day=int(_env_float("APOLAKI_DEMO_MAX_DRAFTS_PER_DAY", cls.max_drafts_per_day)),
                   max_usd_per_day=_env_float("APOLAKI_DEMO_MAX_USD_PER_DAY", cls.max_usd_per_day),
                   per_ip_per_hour=int(_env_float("APOLAKI_DEMO_PER_IP_PER_HOUR", cls.per_ip_per_hour)),
                   usd_per_mtok_in=_env_float("APOLAKI_DEMO_USD_PER_MTOK_IN", cls.usd_per_mtok_in),
                   usd_per_mtok_out=_env_float("APOLAKI_DEMO_USD_PER_MTOK_OUT", cls.usd_per_mtok_out))

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens / 1e6 * self.usd_per_mtok_in + output_tokens / 1e6 * self.usd_per_mtok_out

    def worst_case(self, max_tokens: int) -> float:
        return self.calls_per_draft * self.cost(self.worst_case_input_tokens, max_tokens)


@dataclass
class Limits:
    settings: Settings
    clock: callable = time.time
    day: str = ""
    drafts_today: int = 0
    usd_today: float = 0.0
    by_ip: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.clock()))

    def _roll(self) -> None:
        if self.day != self._today():
            self.day, self.drafts_today, self.usd_today = self._today(), 0, 0.0

    def check(self, ip: str, max_tokens: int) -> str | None:
        """None if a draft may start, else the reason it may not (shown to the visitor)."""
        with self.lock:
            self._roll()
            now = self.clock()
            recent = [t for t in self.by_ip.get(ip, []) if now - t < 3600]
            self.by_ip[ip] = recent
            if len(recent) >= self.settings.per_ip_per_hour:
                return f"That's {self.settings.per_ip_per_hour} drafts this hour from your address. Please try again later."
            if self.drafts_today >= self.settings.max_drafts_per_day:
                return "The demo has reached its limit for today. It resets at midnight UTC."
            if self.usd_today + self.settings.worst_case(max_tokens) > self.settings.max_usd_per_day:
                return "The demo has reached its spending limit for today. It resets at midnight UTC."
            return None

    def record(self, ip: str, input_tokens: int, output_tokens: int) -> float:
        """Count a draft that was attempted (a failed one counts too: it may still have been billed)."""
        with self.lock:
            self._roll()
            usd = self.settings.cost(input_tokens, output_tokens)
            self.drafts_today += 1
            self.usd_today += usd
            self.by_ip.setdefault(ip, []).append(self.clock())
            return usd

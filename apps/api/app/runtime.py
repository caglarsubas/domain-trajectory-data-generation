"""Process-local overrides. Tests install a judge here."""

from app.judge import Judge

judge: Judge | None = None

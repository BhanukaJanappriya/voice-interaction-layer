"""Delivery for wizard codes: a thin layer over otp.sender.

VERIFY_OTP_SENDER (default: OTP_SENDER) names one sender or a comma list,
for example "sms,email". "console" is development only and serves both
channels. The message text comes from the i18n keys otp.sms_body,
otp.email_subject and otp.email_body; when backend/i18n is not there yet, or
a key is missing, the built in text of otp.sender is used. Either way the
validity says "2 minutes" (VERIFY_OTP_TTL_SECONDS), not the older flow's
OTP_TTL_SECONDS.
"""

import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

import config
from otp import sender as base
from otp.sender import ConsoleOtpSender, EmailOtpSender, OtpSender, SmsGatewayOtpSender

try:  # Agent E creates backend/i18n; this works before and after.
    from i18n import t as _t
except Exception:  # noqa: BLE001
    _t = None


def _minutes() -> int:
    return max(1, round(config.VERIFY_OTP_TTL_SECONDS / 60))


def _translate(key: str, language: str, **values) -> Optional[str]:
    if _t is None:
        return None
    try:
        text = _t(key, language, **values)
    except Exception:  # noqa: BLE001
        return None
    return text if isinstance(text, str) and text and text != key else None


def sms_body(code: str, language: str) -> str:
    text = _translate("otp.sms_body", language, code=code, minutes=_minutes())
    if text and code in text:
        return text
    template = base.MESSAGES.get(language, base.MESSAGES["en"])
    return template.format(code=code, minutes=_minutes())


def email_subject(language: str) -> str:
    return _translate("otp.email_subject", language) or base.EMAIL_SUBJECTS.get(language, base.EMAIL_SUBJECTS["en"])


def email_body(code: str, language: str) -> str:
    text = _translate("otp.email_body", language, code=code, minutes=_minutes())
    return text if text and code in text else sms_body(code, language)


class VerifySmsSender(SmsGatewayOtpSender):
    def build_payload(self, destination: str, code: str, language: str) -> dict:
        return base._fill(
            self.body_template,
            {"to": self._to(destination), "message": sms_body(code, language), "sender_id": self.sender_id},
        )


class VerifyEmailSender(EmailOtpSender):
    def send(self, destination: str, code: str, language: str) -> None:
        reason = self.unusable_reason()
        if reason:
            raise base.SenderNotConfigured(reason)
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = destination
        message["Subject"] = email_subject(language)
        message.set_content(email_body(code, language), charset="utf-8")
        try:
            if self.port == 465:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()
                )
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with smtp:
                if self.port != 465:
                    smtp.starttls(context=ssl.create_default_context())
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise base.SenderDeliveryError(f"SMTP delivery failed ({type(exc).__name__})") from None


def sender_for(channel: str) -> Optional[OtpSender]:
    """The sender that delivers on this channel ("sms" or "email"), or None
    when VERIFY_OTP_SENDER names none for it."""
    names = [n.strip() for n in (config.VERIFY_OTP_SENDER or "").split(",") if n.strip()]
    for name in names:
        if name == "console":
            return ConsoleOtpSender()
        if name == "sms" and channel == "sms":
            return VerifySmsSender()
        if name == "email" and channel == "email":
            return VerifyEmailSender()
    return None

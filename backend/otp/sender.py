"""How a one time code reaches a person: the OtpSender interface and its
three implementations.

    ConsoleOtpSender     development only: prints the code to the server
                         console. The ONLY place in the whole package where
                         a code is ever printed, and only when
                         OTP_SENDER=console.
    EmailOtpSender       SMTP with STARTTLS (stdlib smtplib), SMTP_* settings.
    SmsGatewayOtpSender  HTTP POST to SMS_GATEWAY_URL.

get_sender() picks one from config.OTP_SENDER. A sender that is not
configured is still returned (so /auth/methods can list "otp" and
/auth/otp/request can answer 503 with a clear reason) but is_usable() is
False and send() raises SenderNotConfigured.

Errors are typed (SenderError and children) and their text never contains
the code, the destination or any key.

SMS gateway body (SMS_BODY_TEMPLATE): every operator's gateway wants a
slightly different JSON shape. The default is the one the task named,
{"to", "message", "sender_id"}; to adapt it change SMS_BODY_TEMPLATE (the
placeholders {to}, {message} and {sender_id} are replaced in every string
value, nested ones included), or pass body_template= or to_format= when
constructing the sender. to_format chooses how the number is written:
"e164" +94771234567 (default), "msisdn" 94771234567, "national" 0771234567.
The key goes in the header SMS_GATEWAY_AUTH_HEADER (a value for
"Authorization" without a space is sent as "Bearer <key>"). None of this
was verified against a real gateway, because none is configured here.
"""

import smtplib
import ssl
import sys
from abc import ABC, abstractmethod
from email.message import EmailMessage
from typing import Any, Optional

import config
from otp.masking import mask_email, mask_mobile
from url_safety import UnsafeUrlError, safe_session

# Short lines, one per language. {code} and {minutes} are filled in.
MESSAGES = {
    "en": "SLT Mobitel verification code: {code}. Valid for {minutes} minutes. Do not share it with anyone.",
    "si": "SLT Mobitel තහවුරු කිරීමේ කේතය: {code}. විනාඩි {minutes}ක් වලංගුයි. මෙය කිසිවෙකුට නොදෙන්න.",
    "ta": "SLT Mobitel சரிபார்ப்புக் குறியீடு: {code}. {minutes} நிமிடங்கள் செல்லுபடியாகும். இதை யாருடனும் பகிர வேண்டாம்.",
}
EMAIL_SUBJECTS = {
    "en": "Your SLT Mobitel verification code",
    "si": "ඔබගේ SLT Mobitel තහවුරු කිරීමේ කේතය",
    "ta": "உங்கள் SLT Mobitel சரிபார்ப்புக் குறியீடு",
}

SMS_BODY_TEMPLATE: dict = {"to": "{to}", "message": "{message}", "sender_id": "{sender_id}"}


def compose_message(code: str, language: Optional[str]) -> str:
    template = MESSAGES.get(language or "en", MESSAGES["en"])
    return template.format(code=code, minutes=max(1, round(config.OTP_TTL_SECONDS / 60)))


class SenderError(Exception):
    """A code could not be delivered. Safe to log: it holds no secrets."""


class SenderNotConfigured(SenderError):
    """The sender's settings are missing (see OtpSender.unusable_reason)."""


class SenderDeliveryError(SenderError):
    """The sender is configured but the delivery failed (network, SMTP or
    gateway refusal, timeout)."""


class OtpSender(ABC):
    channel: str = ""  # "sms", "email", or "console" (either, for development)
    name: str = ""

    def is_usable(self) -> bool:
        return self.unusable_reason() is None

    def unusable_reason(self) -> Optional[str]:
        """None when ready, otherwise what is missing (setting names only)."""
        return None

    def accepts(self, mobile: Optional[str], email: Optional[str]) -> Optional[tuple]:
        """Which contact this sender would use: (destination, channel
        label) or None when the person has nothing this sender can use."""
        return None

    @abstractmethod
    def send(self, destination: str, code: str, language: str) -> None:
        """Delivers the code. Raises a SenderError on failure."""


class ConsoleOtpSender(OtpSender):
    """DEVELOPMENT ONLY. Prints the code to the server console so the flow
    can be tried without an SMS gateway or mail server. Never configure it
    on a deployment that real customers can reach: anyone with the log can
    sign in as that customer."""

    channel = "console"
    name = "console"

    def accepts(self, mobile, email):
        if mobile:
            return mobile, "sms"
        if email:
            return email, "email"
        return None

    def send(self, destination: str, code: str, language: str) -> None:
        masked = mask_email(destination) if "@" in destination else mask_mobile(destination)
        line = f"[OTP DEV ONLY, OTP_SENDER=console] one time code for {masked}: {code}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:  # pragma: no cover  (an odd console encoding)
            sys.stdout.write(line.encode("ascii", "replace").decode("ascii") + "\n")


class EmailOtpSender(OtpSender):
    channel = "email"
    name = "email"

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, user: Optional[str] = None,
                 password: Optional[str] = None, sender: Optional[str] = None, timeout: float = 10.0):
        self.host = config.SMTP_HOST if host is None else host
        self.port = config.SMTP_PORT if port is None else port
        self.user = config.SMTP_USER if user is None else user
        self.password = config.SMTP_PASSWORD if password is None else password
        self.sender = config.SMTP_FROM if sender is None else sender
        self.timeout = timeout

    def unusable_reason(self) -> Optional[str]:
        missing = [n for n, v in (("SMTP_HOST", self.host), ("SMTP_FROM", self.sender)) if not v]
        return f"missing {', '.join(missing)}" if missing else None

    def accepts(self, mobile, email):
        return (email, "email") if email else None

    def send(self, destination: str, code: str, language: str) -> None:
        reason = self.unusable_reason()
        if reason:
            raise SenderNotConfigured(reason)
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = destination
        message["Subject"] = EMAIL_SUBJECTS.get(language, EMAIL_SUBJECTS["en"])
        message.set_content(compose_message(code, language), charset="utf-8")
        try:
            if self.port == 465:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context())
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with smtp:
                if self.port != 465:
                    smtp.starttls(context=ssl.create_default_context())  # never send credentials in clear
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # The class name only: SMTP error text can echo the recipient.
            raise SenderDeliveryError(f"SMTP delivery failed ({type(exc).__name__})") from None


def _fill(value: Any, values: dict) -> Any:
    if isinstance(value, str):
        for key, replacement in values.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    return value


class SmsGatewayOtpSender(OtpSender):
    channel = "sms"
    name = "sms"

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        auth_header: Optional[str] = None,
        sender_id: Optional[str] = None,
        body_template: Optional[dict] = None,
        to_format: str = "e164",
        timeout: tuple = (5.0, 10.0),
        allow_private: Optional[bool] = None,
    ):
        self.url = config.SMS_GATEWAY_URL if url is None else url
        self._api_key = config.SMS_GATEWAY_API_KEY if api_key is None else api_key
        self.auth_header = config.SMS_GATEWAY_AUTH_HEADER if auth_header is None else auth_header
        self.sender_id = config.SMS_SENDER_ID if sender_id is None else sender_id
        self.body_template = body_template if body_template is not None else SMS_BODY_TEMPLATE
        self.to_format = to_format
        self.timeout = timeout
        self.allow_private = allow_private

    def unusable_reason(self) -> Optional[str]:
        missing = [n for n, v in (("SMS_GATEWAY_URL", self.url), ("SMS_GATEWAY_API_KEY", self._api_key)) if not v]
        return f"missing {', '.join(missing)}" if missing else None

    def accepts(self, mobile, email):
        return (mobile, "sms") if mobile else None

    def _to(self, mobile_e164: str) -> str:
        digits = mobile_e164.lstrip("+")
        if self.to_format == "msisdn":
            return digits
        if self.to_format == "national":
            return "0" + digits[2:] if digits.startswith("94") else digits
        return "+" + digits

    def _header_value(self) -> str:
        if self.auth_header.lower() == "authorization" and " " not in self._api_key:
            return f"Bearer {self._api_key}"
        return self._api_key

    def build_payload(self, destination: str, code: str, language: str) -> dict:
        return _fill(
            self.body_template,
            {"to": self._to(destination), "message": compose_message(code, language), "sender_id": self.sender_id},
        )

    def send(self, destination: str, code: str, language: str) -> None:
        reason = self.unusable_reason()
        if reason:
            raise SenderNotConfigured(reason)
        payload = self.build_payload(destination, code, language)
        try:
            session = safe_session(allow_private=self.allow_private)
            try:
                response = session.post(
                    self.url,
                    json=payload,
                    headers={self.auth_header: self._header_value()},
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            finally:
                session.close()
        except UnsafeUrlError as exc:
            raise SenderDeliveryError(f"the SMS gateway URL is not allowed ({exc})") from None
        except Exception as exc:  # requests and urllib3 errors: class name only, they can echo the URL
            raise SenderDeliveryError(f"SMS gateway request failed ({type(exc).__name__})") from None
        if not 200 <= response.status_code < 300:
            raise SenderDeliveryError(f"SMS gateway answered HTTP {response.status_code}")


def get_sender() -> OtpSender:
    """The sender chosen by OTP_SENDER (console, email, sms). An unknown
    value gives a sender that is not usable, so a typo is a clear 503 and
    never a silent fallback to printing codes on the console."""
    choice = (config.OTP_SENDER or "").strip().lower()
    if choice == "console":
        return ConsoleOtpSender()
    if choice == "email":
        return EmailOtpSender()
    if choice == "sms":
        return SmsGatewayOtpSender()
    return _UnknownSender(choice)


class _UnknownSender(OtpSender):
    channel = ""
    name = "unknown"

    def __init__(self, choice: str):
        self._choice = choice

    def unusable_reason(self) -> Optional[str]:
        return f"OTP_SENDER={self._choice!r} is not one of console, email, sms"

    def send(self, destination: str, code: str, language: str) -> None:
        raise SenderNotConfigured(self.unusable_reason() or "")

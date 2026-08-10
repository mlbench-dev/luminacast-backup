"""Transactional email (password reset, etc.) via plain SMTP.

If SMTP_HOST isn't configured the email is logged instead of sent, so
local development works without real mail credentials.
"""
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import settings

logger = logging.getLogger(__name__)


def _send_sync(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())


async def send_email(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    if not settings.SMTP_HOST:
        logger.warning(
            "SMTP not configured (SMTP_HOST empty) — logging email instead of sending. to=%s subject=%r",
            to_email, subject,
        )
        logger.info("Email body (dev fallback):\n%s", text_body)
        return
    await asyncio.to_thread(_send_sync, to_email, subject, html_body, text_body)


async def send_password_reset_email(to_email: str, reset_link: str) -> None:
    subject = "Reset your Luminacast password"
    text_body = (
        "We received a request to reset your Luminacast password.\n\n"
        f"Reset your password: {reset_link}\n\n"
        "This link expires in 30 minutes. If you didn't request this, you can safely ignore this email."
    )
    html_body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:0 auto;color:#1a1a1a;">
      <h2 style="margin-bottom:8px;">Reset your password</h2>
      <p>We received a request to reset your Luminacast password.</p>
      <p style="margin:24px 0;">
        <a href="{reset_link}" style="display:inline-block;padding:12px 24px;background:#6d28d9;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">Reset Password</a>
      </p>
      <p style="color:#666;font-size:13px;">This link expires in 30 minutes. If you didn't request this, you can safely ignore this email.</p>
      <p style="color:#999;font-size:12px;">If the button doesn't work, copy and paste this link:<br>{reset_link}</p>
    </div>
    """
    await send_email(to_email, subject, html_body, text_body)


async def send_team_invite_email(
    to_email: str, accept_link: str, owner_label: str, role: str,
) -> None:
    subject = f"{owner_label} invited you to their Luminacast workspace"
    role_label = role.capitalize()
    text_body = (
        f"{owner_label} invited you to join their Luminacast workspace as a {role_label}.\n\n"
        f"Accept the invite: {accept_link}\n\n"
        "This link expires in 7 days."
    )
    html_body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:0 auto;color:#1a1a1a;">
      <h2 style="margin-bottom:8px;">You're invited</h2>
      <p><strong>{owner_label}</strong> invited you to join their Luminacast workspace as a <strong>{role_label}</strong>.</p>
      <p style="margin:24px 0;">
        <a href="{accept_link}" style="display:inline-block;padding:12px 24px;background:#6d28d9;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">Accept Invite</a>
      </p>
      <p style="color:#666;font-size:13px;">This link expires in 7 days.</p>
      <p style="color:#999;font-size:12px;">If the button doesn't work, copy and paste this link:<br>{accept_link}</p>
    </div>
    """
    await send_email(to_email, subject, html_body, text_body)

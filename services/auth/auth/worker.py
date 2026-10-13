"""Auth worker: consumes the auth.emails stream and sends the emails over SMTP, publishes
events left in the outbox (Redis was down when the request tried), and runs the hourly cleanup
(auth.cleanup: unverified accounts, old refresh tokens and email codes).

Run with `python -m auth.worker`.
"""

import asyncio
import logging
from email.message import EmailMessage

import aiosmtplib
from tpsarena_common.events import CodeEmail, Event
from tpsarena_common.streams import StreamConsumer, consumer_redis

from auth import cleanup
from auth.config import get_settings
from auth.events import outbox

log = logging.getLogger("auth.worker")

SUBJECTS = {
    "verify": "Your TPSArena verification code",
    "reset": "Your TPSArena password reset code",
}
BODIES = {
    "verify": "Hi {username},\n\nYour verification code is {code}.\nIt expires in {ttl} minutes.\n",
    "reset": "Hi {username},\n\nYour password reset code is {code}.\nIt expires in {ttl} minutes.\n"
    "If you didn't ask for it, you can ignore this email.\n",
}


async def send_email(event: CodeEmail) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = event.email
    message["Subject"] = SUBJECTS[event.kind]
    message.set_content(BODIES[event.kind].format(username=event.username, code=event.code, ttl=settings.code_ttl_minutes))
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_starttls,
    )
    log.info("Sent %s email (event %s)", event.kind, event.event_id)


async def handle(event: Event) -> None:
    if isinstance(event, CodeEmail):
        await send_email(event)


async def run() -> None:
    settings = get_settings()
    consumer = StreamConsumer(consumer_redis(settings.redis_url), "auth", "auth-worker", [settings.emails_stream], handle)
    await asyncio.gather(consumer.run(), outbox.run(), cleanup.run())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Gunluk e-posta ozeti: Gmail (IMAP) -> Gemini ile kategorizasyon -> e-posta (SMTP)."""

import argparse
import html
import imaplib
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"

DEFAULT_CATEGORIES = (
    "Is, Kisisel, Fatura/Finans, Reklam/Promosyon, Bildirim/Otomatik, "
    "Spam/Supheli, Diger"
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overview": {"type": "STRING"},
        "categories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "summary": {"type": "STRING"},
                    "emails": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "subject": {"type": "STRING"},
                                "from": {"type": "STRING"},
                                "summary": {"type": "STRING"},
                                "priority": {
                                    "type": "STRING",
                                    "enum": ["yuksek", "orta", "dusuk"],
                                },
                            },
                            "required": ["subject", "from", "summary", "priority"],
                        },
                    },
                },
                "required": ["name", "summary", "emails"],
            },
        },
    },
    "required": ["overview", "categories"],
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"digest-{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_body(msg) -> str:
    plain, rich = [], []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue
            (plain if ctype == "text/plain" else rich).append(decoded)
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            decoded = ""
        if msg.get_content_type() == "text/html":
            rich.append(decoded)
        else:
            plain.append(decoded)

    if plain:
        return re.sub(r"\s+", " ", "\n".join(plain)).strip()
    if rich:
        return strip_html("\n".join(rich))
    return ""


def _fetch_emails_once(
    hours: int,
    host: str,
    port: int,
    user: str,
    password: str,
    folder: str,
    imap_timeout: int,
) -> list[dict]:
    since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
    emails: list[dict] = []

    imap = imaplib.IMAP4_SSL(host, port, timeout=imap_timeout)
    try:
        imap.login(user, password)
        imap.select(folder, readonly=True)
        status, data = imap.uid("search", None, "SINCE", since)
        if status != "OK":
            raise RuntimeError(f"IMAP arama basarisiz: {status}")
        uids = data[0].split()
        logging.info("%s adet mesaj bulundu, filtreleniyor...", len(uids))

        cutoff = datetime.now().astimezone() - timedelta(hours=hours)
        for uid in uids:
            status, fetched = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            msg = message_from_bytes(fetched[0][1])

            try:
                msg_date = parsedate_to_datetime(msg.get("Date"))
                if msg_date.tzinfo is None:
                    msg_date = msg_date.astimezone()
            except Exception:
                msg_date = None
            if msg_date and msg_date < cutoff:
                continue

            body = extract_body(msg)
            emails.append(
                {
                    "subject": decode_mime(msg.get("Subject")) or "(konu yok)",
                    "from": decode_mime(msg.get("From")) or "(bilinmiyor)",
                    "date": msg_date.strftime("%d.%m.%Y %H:%M") if msg_date else "",
                    "body": body[:1500],
                }
            )
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return emails


def fetch_emails(hours: int) -> list[dict]:
    host = cfg("IMAP_HOST", "imap.gmail.com")
    port = int(cfg("IMAP_PORT", "993"))
    user = cfg("GMAIL_ADDRESS")
    password = cfg("GMAIL_APP_PASSWORD")
    folder = cfg("MAIL_FOLDER", "INBOX")
    imap_timeout = int(cfg("IMAP_TIMEOUT", "60"))
    retries = int(cfg("IMAP_RETRIES", "3"))

    for attempt in range(retries + 1):
        logging.info(
            "IMAP baglaniyor: %s@%s, son %s saat (deneme %s/%s)",
            user,
            host,
            hours,
            attempt + 1,
            retries + 1,
        )
        try:
            emails = _fetch_emails_once(
                hours, host, port, user, password, folder, imap_timeout
            )
            break
        except (
            imaplib.IMAP4.abort,
            imaplib.IMAP4.error,
            ssl.SSLError,
            OSError,
        ) as exc:
            if attempt >= retries:
                raise RuntimeError(
                    f"IMAP baglantisi {retries + 1} denemede basarisiz oldu: {exc}"
                ) from exc
            wait = min(2 ** attempt * 5, 60)
            logging.warning(
                "IMAP hatasi (%s), %ss sonra tekrar denenecek", exc, wait
            )
            time.sleep(wait)

    emails.sort(key=lambda e: e["date"], reverse=True)
    logging.info("%s adet yeni e-posta analiz edilecek", len(emails))
    return emails


def build_prompt(emails: list[dict]) -> str:
    lines = []
    for i, mail in enumerate(emails, 1):
        lines.append(
            f"--- E-posta {i} ---\n"
            f"Kimden: {mail['from']}\n"
            f"Konu: {mail['subject']}\n"
            f"Tarih: {mail['date']}\n"
            f"Icerik: {mail['body']}\n"
        )
    joined = "\n".join(lines)
    return (
        "Asagidaki e-postalari Turkce olarak analiz et ve kategorilere ayir.\n"
        f"Kullanilacak kategoriler (gerekirse digerlerini birlestir): {DEFAULT_CATEGORIES}.\n"
        "Her e-posta icin: kimden geldigi, kisa bir ozet (1-2 cumle) ve oncelik "
        "(yuksek/orta/dusuk). Onemli tarihler, tutarlar, aksiyon gerektiren istekler "
        "varsa ozette belirt. Ayni kategorideki e-postalari grupla. En ustte "
        "genel bir durum ozeti (overview) yaz. Sadece istenen JSON semasina uygun "
        "yanit ver, baska aciklama ekleme.\n\n"
        f"E-postalar:\n{joined}"
    )


def model_candidates() -> list[str]:
    primary = cfg("GEMINI_MODEL", "gemini-3.8-flash")
    fallbacks = cfg(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.8-flash,gemini-3.5-flash,gemini-flash-latest,gemini-2.5-flash-lite",
    )
    ordered = [primary, *[m.strip() for m in fallbacks.split(",") if m.strip()]]
    seen, result = set(), []
    for model in ordered:
        if model and model not in seen:
            seen.add(model)
            result.append(model)
    return result


def call_gemini_once(model: str, prompt: str, api_key: str) -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = int(cfg("GEMINI_TIMEOUT", "75"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Beklenmeyen Gemini yaniti: {body}") from exc

    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def call_gemini(prompt: str) -> dict:
    api_key = cfg("GEMINI_API_KEY")
    retryable = {429, 500, 502, 503, 504}
    attempts_per_model = int(cfg("GEMINI_RETRIES", "2"))
    last_error: Exception | None = None

    for model in model_candidates():
        for attempt in range(attempts_per_model + 1):
            logging.info(
                "Gemini API cagriliyor (model: %s, deneme: %s)",
                model,
                attempt + 1,
            )
            try:
                return call_gemini_once(model, prompt, api_key)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"Gemini API hatasi ({exc.code}): {detail}")
                if exc.code not in retryable:
                    logging.warning("%s modeli kalici hata verdi, atlaniyor", model)
                    break
                wait = 2 ** attempt
                logging.warning(
                    "%s modeli %s dondurdu, %ss sonra tekrar denenecek",
                    model,
                    exc.code,
                    wait,
                )
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                wait = 2 ** attempt
                logging.warning(
                    "%s modeli baglanti hatasi (%s), %ss sonra tekrar denenecek",
                    model,
                    exc,
                    wait,
                )
                time.sleep(wait)

    raise RuntimeError(f"Tum Gemini modelleri basarisiz oldu: {last_error}")


def build_report(data: dict, emails: list[dict]) -> tuple[str, str]:
    now = datetime.now()
    overview = data.get("overview", "")
    categories = data.get("categories", [])

    text_parts = [
        f"Gunluk E-posta Ozeti - {now:%d.%m.%Y}",
        f"Toplam {len(emails)} e-posta analiz edildi.",
        "",
        "GENEL DURUM",
        overview,
        "",
    ]
    rows = []
    for cat in categories:
        name = cat.get("name", "Diger")
        summary = cat.get("summary", "")
        cat_emails = cat.get("emails", [])
        text_parts.append(f"== {name} ({len(cat_emails)}) ==")
        if summary:
            text_parts.append(summary)
        for mail in cat_emails:
            priority = mail.get("priority", "orta")
            text_parts.append(
                f"- [{priority}] {mail.get('subject', '')} | {mail.get('from', '')}\n"
                f"  {mail.get('summary', '')}"
            )
            rows.append(
                "<tr>"
                f"<td class='cat'>{html.escape(name)}</td>"
                f"<td>{html.escape(mail.get('subject', ''))}<br>"
                f"<span class='from'>{html.escape(mail.get('from', ''))}</span></td>"
                f"<td>{html.escape(mail.get('summary', ''))}</td>"
                f"<td class='prio {html.escape(priority)}'>{html.escape(priority)}</td>"
                "</tr>"
            )
        text_parts.append("")

    category_cards = []
    for cat in categories:
        name = cat.get("name", "Diger")
        summary = cat.get("summary", "")
        count = len(cat.get("emails", []))
        category_cards.append(
            "<div class='card'>"
            f"<h2>{html.escape(name)} <span class='count'>{count}</span></h2>"
            f"<p class='summary'>{html.escape(summary)}</p></div>"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         background:#f4f5f7; color:#1f2328; margin:0; padding:24px; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .meta {{ color:#6b7280; font-size:13px; margin-bottom:20px; }}
  .overview {{ background:#fff; border-left:4px solid #2563eb; padding:14px 18px;
               border-radius:8px; margin-bottom:18px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }}
  .card {{ background:#fff; border-radius:8px; padding:12px 16px; flex:1 1 200px;
           min-width:180px; }}
  .card h2 {{ font-size:14px; margin:0 0 6px; }}
  .count {{ background:#e5e7eb; color:#374151; border-radius:10px; padding:1px 8px;
            font-size:12px; }}
  .card .summary {{ font-size:12px; color:#4b5563; margin:0; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:8px;
           overflow:hidden; font-size:13px; }}
  th {{ text-align:left; background:#111827; color:#fff; padding:10px; font-size:12px; }}
  td {{ padding:10px; border-bottom:1px solid #eef0f3; vertical-align:top; }}
  .cat {{ font-weight:600; color:#2563eb; white-space:nowrap; }}
  .from {{ color:#6b7280; font-size:12px; }}
  .prio {{ font-weight:600; text-transform:capitalize; white-space:nowrap; }}
  .prio.yuksek {{ color:#dc2626; }}
  .prio.orta {{ color:#d97706; }}
  .prio.dusuk {{ color:#059669; }}
</style></head><body><div class="wrap">
<h1>Gunluk E-posta Ozeti</h1>
<div class="meta">{now:%d.%m.%Y %H:%M} &middot; {len(emails)} e-posta analiz edildi</div>
<div class="overview">{html.escape(overview)}</div>
<div class="cards">{''.join(category_cards)}</div>
<table>
<tr><th>Kategori</th><th>E-posta</th><th>Ozet</th><th>Oncelik</th></tr>
{''.join(rows)}
</table>
</div></body></html>"""

    return "\n".join(text_parts), html_doc


def send_email(subject: str, text_body: str, html_body: str) -> None:
    host = cfg("SMTP_HOST", "smtp.gmail.com")
    port = int(cfg("SMTP_PORT", "465"))
    user = cfg("GMAIL_ADDRESS")
    password = cfg("GMAIL_APP_PASSWORD")
    recipient = cfg("DIGEST_TO") or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Gunluk E-posta Ozeti", user))
    msg["To"] = recipient
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    logging.info("Ozet e-postasi gonderiliyor: %s", recipient)
    with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    logging.info("E-posta basariyla gonderildi")


def validate_config() -> None:
    required = ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "GEMINI_API_KEY"]
    missing = [name for name in required if not cfg(name)]
    if missing:
        raise SystemExit(
            "Eksik yapilandirma: " + ", ".join(missing) + f"\n{ENV_FILE} dosyasini doldurun."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Gunluk e-posta ozeti")
    parser.add_argument("--hours", type=int, default=int(os.environ.get("LOOKBACK_HOURS", "24")))
    parser.add_argument("--dry-run", action="store_true", help="E-posta gonderme, raporu kaydet")
    args = parser.parse_args()

    load_env(ENV_FILE)
    setup_logging()
    validate_config()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    recipients = cfg("DIGEST_TO") or cfg("GMAIL_ADDRESS")

    try:
        emails = fetch_emails(args.hours)
        if not emails:
            logging.info("Yeni e-posta yok, ozet gonderilmedi.")
            return 0

        data = call_gemini(build_prompt(emails))
        text_body, html_body = build_report(data, emails)

        report_path = REPORT_DIR / f"digest-{datetime.now():%Y-%m-%d}.html"
        report_path.write_text(html_body, encoding="utf-8")
        logging.info("Rapor kaydedildi: %s", report_path)

        timestamp = datetime.now().strftime("%d.%m.%Y")
        subject = f"Gunluk E-posta Ozeti - {timestamp} ({len(emails)} mail)"

        if args.dry_run:
            print(text_body)
            logging.info("--dry-run: e-posta gonderilmedi")
        else:
            send_email(subject, text_body, html_body)
    except Exception as exc:
        logging.exception("Ozet olusturulamadi: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

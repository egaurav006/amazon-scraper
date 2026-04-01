#!/usr/bin/env python3
"""
Sends email with amazon_data.csv attached.
Reads Gmail credentials from environment variables.
"""

import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta


def send_email():
    # ── Read credentials ──
    email_addr = os.environ.get("EMAIL_ADDRESS", "").strip()
    email_pass = os.environ.get("EMAIL_APP_PASSWORD", "").strip()

    if not email_addr or not email_pass:
        print("⚠️ Email credentials not set. Skipping email.")
        return

    # ── Read summary ──
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).strftime("%d-%b-%Y")

    total = req = pw = fail = "?"
    elapsed = "?"
    try:
        with open("run_summary.txt", "r") as f:
            lines = f.read().strip().split("\n")
            total = lines[0]
            elapsed = lines[1]
            req = lines[2]
            pw = lines[3]
            fail = lines[4]
    except Exception:
        pass

    # ── Check CSV exists ──
    csv_file = "amazon_data.csv"
    if not os.path.exists(csv_file):
        print(f"❌ {csv_file} not found. Skipping email.")
        return

    csv_size = os.path.getsize(csv_file)
    csv_kb = f"{csv_size / 1024:.1f} KB"

    # ── Build email ──
    msg = MIMEMultipart()
    msg["From"] = email_addr
    msg["To"] = email_addr
    msg["Subject"] = f"📊 Amazon Scraper Report — {today}"

    body = f"""Hi,

Your daily Amazon scraper has completed.

━━━━━━━━ Summary ━━━━━━━━
📅 Date:         {today}
📋 Total ASINs:  {total}
⏱️ Time:         {elapsed} minutes
⚡ Requests:     {req}
🌐 Playwright:   {pw}
❌ Failed:       {fail}
📎 CSV Size:     {csv_kb}
━━━━━━━━━━━━━━━━━━━━━━━━

The output CSV is attached below.

— Amazon Scraper Bot 🤖
"""

    msg.attach(MIMEText(body, "plain"))

    # ── Attach CSV ──
    try:
        with open(csv_file, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename=amazon_data_{today}.csv"
            )
            msg.attach(part)
    except Exception as e:
        print(f"⚠️ Could not attach CSV: {e}")

    # ── Send ──
    try:
        print(f"📧 Sending email to {email_addr}...")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(email_addr, email_pass)
            server.send_message(msg)
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Email failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    send_email()

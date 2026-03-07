import argparse
import base64
import logging
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

OAUTH_CREDENTIALS_FILE = os.path.expanduser("./google_credentials.json")
OAUTH_TOKEN_FILE = os.path.expanduser("./google_token.json")

REQUIRED_ENV_VARS = ["MY_EMAIL", "BOSS_EMAIL", "EMAIL_SUBJECT", "EMAIL_BODY", "RECEIPT_NAME"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def validate_env():
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def get_gmail_service():
    creds = None

    if os.path.exists(OAUTH_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(OAUTH_TOKEN_FILE, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                OAUTH_CREDENTIALS_FILE, GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_dir = os.path.dirname(OAUTH_TOKEN_FILE)
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)
        with open(OAUTH_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def trigger_login_email(email: str):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        logging.info(f"Navigating to member hub")
        page.goto("https://switchyards.com/manage-account")

        page.get_by_role("link", name="Access payment details.").wait_for()

        with context.expect_page() as login_page_info:
            page.get_by_role("link", name="Access payment details.").click()

        login_page = login_page_info.value
        logging.debug("Login page opened")

        login_page.get_by_label("Email").fill(email)
        login_page.get_by_role("button", name="Send").click()
        login_page.wait_for_selector("text=Check your email", timeout=10000)
        logging.info("Login email triggered")

        browser.close()


def fetch_magic_link(service) -> str | None:
    query = f"from:billing@switchyards.com subject:Your customer portal login link"

    deadline = time.time() + 120
    while time.time() < deadline:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=5
        ).execute()

        messages = results.get("messages", [])
        if messages:
            msg = service.users().messages().get(
                userId="me",
                id=messages[0]["id"],
                format="full"
            ).execute()

            body = extract_email_body(msg)
            if body:
                urls = re.findall(
                    r'https://membership\.switchyards\.com/p/session/[^\s"\'<>]+', body
                )
                if urls:
                    logging.info(f"Magic link found: {urls[0]}")
                    if urls[0].endswith(")"):
                        url = urls[0][:-1]
                        return url
                    return urls[0]

        logging.info(f"Waiting for magic link email")
        time.sleep(15)

    return None


def get_pdf_path(magic_link: str, month_year: str) -> str:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        member_hub_page = context.new_page()

        logging.info("Navigating to magic link")
        member_hub_page.goto(magic_link)

        member_hub_page.wait_for_selector('[data-testid="hip-link"]')

        invoice_url = member_hub_page.locator('[data-testid="hip-link"]').first.get_attribute("href")

        member_hub_page.goto(invoice_url)

        with member_hub_page.expect_download() as download_info:
            member_hub_page.get_by_text("Download receipt").click()

        download = download_info.value

        out_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(out_dir, f"{os.getenv("RECEIPT_NAME")} - {month_year}.pdf")
        download.save_as(pdf_path)

        logging.info(f"Receipt downloaded: {pdf_path}")
        browser.close()


        return pdf_path


def extract_email_body(msg: dict) -> str:
    payload = msg.get("payload", {})

    def decode_part(part):
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        return ""

    def walk(part):
        mime_type = part.get("mimeType", "")
        if mime_type in ("text/plain", "text/html"):
            return decode_part(part)
        for sub in part.get("parts", []):
            result = walk(sub)
            if result:
                return result
        return ""

    return walk(payload)


def send_email(service, pdf_path: str, month_year: str):
    msg = MIMEMultipart()
    msg["To"] = os.getenv("BOSS_EMAIL")
    msg["From"] = os.getenv("MY_EMAIL")
    msg["Subject"] = f"{os.getenv("EMAIL_SUBJECT")} - {month_year}"

    body = os.getenv("EMAIL_BODY").replace("\\n", "\n").format(month_year=month_year)
    msg.attach(MIMEText(body, "plain"))

    with open(pdf_path, "rb") as file:
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(file.read())
        encoders.encode_base64(attachment)
        filename = os.path.basename(pdf_path)
        attachment.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(attachment)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-only", action="store_true", help="Download receipt without emailing it")
    args = parser.parse_args()

    validate_env()
    month_year = datetime.now().strftime("%B %Y")
    logging.info(f"***********************************************")
    logging.info(f"Switchyards Receipt Automation — {month_year}")
    logging.info(f"***********************************************")

    logging.info("Authenticating with Gmail")
    gmail = get_gmail_service()
    logging.info("Gmail authenticated")

    trigger_login_email(os.getenv("MY_EMAIL"))

    logging.info("Polling Gmail for magic link")
    magic_link = fetch_magic_link(gmail)
    if not magic_link:
        raise RuntimeError("Timed out waiting for magic link email. Check your inbox manually.")

    pdf_path = get_pdf_path(magic_link, month_year)

    if args.download_only:
        receipts_dir = Path("receipts")
        receipts_dir.mkdir(exist_ok=True)
        destination = receipts_dir / Path(pdf_path).name
        Path(pdf_path).rename(destination)
        logging.info(f"Receipt saved to {destination}")
        return

    logging.info(f"Sending receipt to {os.getenv("BOSS_EMAIL")}")
    send_email(gmail, pdf_path, month_year)

    logging.info(f"Done! Receipt for {month_year} sent successfully.")


if __name__ == "__main__":
    main()

# switchyards-receipt-grabber

Automates monthly receipt collection from Switchyards and emails it to your boss.

## What it does

1. Navigates to the Switchyards member hub and triggers a magic link login email
1. Polls Gmail for the magic link
1. Uses Playwright to log in, find the latest invoice, and download the PDF receipt
1. Emails the PDF to a recipient via Gmail

## Setup

### Prerequisites

- Python >= 3.14
- [uv](https://github.com/astral-sh/uv)
- A Google Cloud project with the Gmail API enabled
- OAuth 2.0 credentials downloaded as `google_credentials.json`

### Install dependencies

```bash
uv sync
uv run playwright install chromium
```

### Google OAuth

On first run, a browser window will open for you to authenticate with Google. 
A `google_token.json` file will be created to store the token for future runs.

### Environment variables

Create a `.env` file:

```bash
cp .env.example .env
```

## Running

```bash
source .env
uv run python switchyards_receipt.py
```

## Scheduling (cron)

To run on the 1st of every month:

```
0 9 1 * * cd /path/to/switchyards-receipt-fetcher && source .env && uv run python switchyards_receipt.py
```

FROM python:3.14-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync

RUN uv run playwright install-deps chromium && uv run playwright install chromium

COPY switchyards_receipt.py .

CMD ["uv", "run", "python", "switchyards_receipt.py"]

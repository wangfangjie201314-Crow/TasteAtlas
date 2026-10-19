FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# The host must mount the EXISTING private ledger and supply secrets privately.
# Missing storage fails closed; this image never grants another request budget.
USER 10001:10001
CMD ["sh", "-c", "exec uvicorn backend.hosted:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --no-access-log --no-proxy-headers"]

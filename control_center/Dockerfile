# "Control Center" Home Assistant add-on (Python / Flask).
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    INGRESS_PORT=4000 \
    USER_PORT=8099

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# config.yaml is copied too so the app can read its own version at runtime
# (shown as the build label / asset ?v=); without it the version reads "dev".
COPY app.py core.py config.py errors.py access.py ha.py users.json config.yaml ./
COPY routes/ ./routes/
COPY static/ ./static/

# 4000 = management (Ingress, not published); 8099 = user dashboard (published).
EXPOSE 8099
# One worker (so a single HA WebSocket + shared state cache), many threads so
# long-lived SSE streams don't starve normal requests. Binds BOTH ports; the
# request's port decides management vs. user.
CMD ["gunicorn", "--bind", "0.0.0.0:4000", "--bind", "0.0.0.0:8099", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "16", \
     "--timeout", "0", "app:app"]

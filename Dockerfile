# canvas-mock: stdlib-only mock of a subset of the Canvas LMS REST API.
# No third-party dependencies, so the image is tiny and self-contained.
FROM python:3.12-slim

WORKDIR /app
COPY canvas_mock.py .

# Overridable at runtime: PORT (listen port), CANVAS_MOCK_COURSE_ID (course id).
ENV PORT=8913
EXPOSE 8913

# Lightweight healthcheck against the unauthenticated /healthz endpoint.
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8913'))" || exit 1

CMD ["python3", "canvas_mock.py"]

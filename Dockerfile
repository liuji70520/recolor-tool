FROM python:3.11-slim

WORKDIR /app

# PyMuPDF 渲染与字体支持所需的基础库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY recolor.py /app/recolor.py
COPY web/ /app/web/

RUN pip install --no-cache-dir -r /app/web/requirements.txt

WORKDIR /app/web
ENV HOST=0.0.0.0
ENV PORT=7860
EXPOSE 7860

CMD ["waitress-serve", "--port=7860", "app:app"]

FROM python:3.12-slim
WORKDIR /app
ENV PORT=3000
EXPOSE 3000 8080
COPY server.py .
COPY requirements.txt .
RUN mkdir -p build
COPY index.html ./build/index.html
CMD ["python", "-u", "server.py"]

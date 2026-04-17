FROM python:3.11-slim
WORKDIR /app
COPY interceptor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY interceptor /app/interceptor
EXPOSE 8080
ENTRYPOINT ["mitmdump", "-s", "interceptor/mitm_addon.py", "--listen-port", "8080"]
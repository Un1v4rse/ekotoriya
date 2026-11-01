# -*- coding: utf-8 -*-
"""Ожидание доступности сервера, докачка _deploy.tar.gz и пересборка контейнера."""
import os
import time

import paramiko

HOST = "185.56.162.91"
USER = "root"
PASSWORD = "4wQ9SaVk8qAN10c"
LOCAL = "_deploy.tar.gz"
REMOTE = "/opt/ekotoriya/_deploy.tar.gz"

SIZE = os.path.getsize(LOCAL)


def connect(retries=60, delay=30):
    for i in range(retries):
        try:
            t = paramiko.Transport((HOST, 22))
            t.banner_timeout = 30
            t.connect(username=USER, password=PASSWORD)
            return t
        except Exception as e:
            print(f"[{i+1}/{retries}] ssh не доступен: {e}", flush=True)
            time.sleep(delay)
    raise SystemExit("ssh так и не поднялся")


# 1) ждём ssh
print("жду ssh...", flush=True)
transport = connect()

# 2) докачка архива
for attempt in range(20):
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            offset = sftp.stat(REMOTE).st_size
        except IOError:
            offset = 0
        if offset == SIZE:
            print("архив уже на месте", flush=True)
            break
        if offset > SIZE:
            offset = 0
        with open(LOCAL, "rb") as f, sftp.open(REMOTE, "ab" if offset else "wb") as out:
            f.seek(offset)
            sent = offset
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sent += len(chunk)
                if sent % (5 * 1024 * 1024) < 256 * 1024:
                    print(f"  {sent}/{SIZE} ({sent*100//SIZE}%)", flush=True)
        sftp.close()
        print("загрузка завершена", flush=True)
        break
    except Exception as e:
        print(f"upload attempt {attempt+1}: {e}", flush=True)
        try:
            transport.close()
        except Exception:
            pass
        time.sleep(10)
        transport = connect(retries=10, delay=15)

# 3) распаковка + пересборка
cmd = (
    "cd /opt/ekotoriya && tar -xzf _deploy.tar.gz && "
    "sed -i 's/FORCE_HTTPS: \"1\"/FORCE_HTTPS: \"0\"/' docker-compose.yml && "
    "docker compose up -d --build app && echo DEPLOY_OK"
)
chan = transport.open_session()
chan.exec_command(cmd)
out = b""
while True:
    if chan.recv_ready():
        out += chan.recv(65536)
    if chan.exit_status_ready():
        while chan.recv_ready():
            out += chan.recv(65536)
        break
    time.sleep(2)
print(out.decode("utf-8", "replace")[-3000:], flush=True)
print("exit:", chan.recv_exit_status(), flush=True)
transport.close()

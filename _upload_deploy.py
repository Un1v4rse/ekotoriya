# -*- coding: utf-8 -*-
"""SFTP-загрузка _deploy.tar.gz на сервер с докачкой при обрыве соединения."""
import os
import sys
import time

import paramiko

HOST = "185.56.162.91"
USER = "root"
PASSWORD = "4wQ9SaVk8qAN10c"
LOCAL = "_deploy.tar.gz"
REMOTE = "/opt/ekotoriya/_deploy.tar.gz"
FRESH_UPLOAD = True  # перезаписать с нуля


def main():
    size = os.path.getsize(LOCAL)
    for attempt in range(10):
        try:
            t0 = time.time()
            transport = paramiko.Transport((HOST, 22))
            transport.connect(username=USER, password=PASSWORD)
            transport.banner_timeout = 30
            sftp = paramiko.SFTPClient.from_transport(transport)
            offset = 0
            if not FRESH_UPLOAD:
                try:
                    offset = sftp.stat(REMOTE).st_size
                except IOError:
                    offset = 0
            if offset >= size:
                print("already uploaded")
                sftp.close()
                transport.close()
                return
            with open(LOCAL, "rb") as f, sftp.open(REMOTE, "ab" if offset else "wb") as out:
                f.seek(offset)
                sent = offset
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    sent += len(chunk)
                    print(f"  {sent}/{size} ({sent*100//size}%)", flush=True)
            sftp.close()
            transport.close()
            print(f"OK {size} bytes in {time.time()-t0:.0f}s")
            return
        except Exception as e:
            print(f"attempt {attempt+1} failed: {e}", flush=True)
            time.sleep(3)
    sys.exit(1)


if __name__ == "__main__":
    main()

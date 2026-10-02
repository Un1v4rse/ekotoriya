# -*- coding: utf-8 -*-
"""Recursively upload local ./data to /opt/ekotoriya/data on the server via SFTP."""
import os, sys, paramiko

HOST, USER, PASS = '185.56.162.91', 'root', '4wQ9SaVk8qAN10c'
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
REMOTE = '/opt/ekotoriya/data'

def upload_dir(sftp, local, remote):
    try:
        sftp.mkdir(remote)
    except IOError:
        pass
    for name in os.listdir(local):
        lp = os.path.join(local, name)
        rp = remote + '/' + name
        if os.path.isdir(lp):
            upload_dir(sftp, lp, rp)
        else:
            sftp.put(lp, rp)
            print('OK', rp, os.path.getsize(lp))

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASS, timeout=20)
    sftp = ssh.open_sftp()
    upload_dir(sftp, LOCAL, REMOTE)
    sftp.close()
    ssh.close()
    print('DONE')

if __name__ == '__main__':
    main()

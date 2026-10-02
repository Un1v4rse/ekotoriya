# -*- coding: utf-8 -*-
"""Helper for SSH commands to the deploy server via paramiko."""
import sys, paramiko

HOST, USER, PASS = '185.56.162.91', 'root', '4wQ9SaVk8qAN10c'

def main():
    cmd = sys.argv[1]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASS, timeout=20)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
    out = stdout.read().decode('utf-8', 'replace')
    err = stderr.read().decode('utf-8', 'replace')
    code = stdout.channel.recv_exit_status()
    ssh.close()
    sys.stdout.write(out)
    if err:
        sys.stdout.write('--- STDERR ---\n' + err)
    sys.exit(code)

if __name__ == '__main__':
    main()

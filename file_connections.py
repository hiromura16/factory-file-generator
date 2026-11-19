"""Direct SMB, FTP(S), SFTP connections and explicitly pre-mounted NFS."""

from __future__ import annotations

import base64
import ftplib
import hashlib
import io
import posixpath
import socket
import uuid
from pathlib import Path, PurePosixPath

from generation import GenerationError
from connection_security import connection_file, secret, tls_context


def check_nfs(path, expected=""):
    resolved = Path(path).resolve()
    matches = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        parts = left.split()
        mount = Path(parts[4].replace("\\040", " "))
        details = right.split()
        if resolved.is_relative_to(mount):
            matches.append((len(str(mount)), details[0], details[1]))
    if not matches or max(matches)[1] not in {"nfs", "nfs4"}:
        raise GenerationError(
            "NFS共有は利用環境のホストにマウントし、アプリへ/network/nfs等で渡してください。指定先はNFSファイルシステムではありません"
        )
    _, kind, source = max(matches)
    if expected and source != expected:
        raise GenerationError("マウントされているNFS共有が指定した接続元と一致しません")
    return {"filesystem": kind, "source": source}


class ImplicitFTP(ftplib.FTP_TLS):
    def connect(self, host="", port=0, timeout=-999, source_address=None):
        self.host = host or self.host
        self.port = port or 990
        if timeout != -999:
            self.timeout = timeout
        self.sock = self.context.wrap_socket(
            socket.create_connection(
                (self.host, self.port), self.timeout, source_address
            ),
            server_hostname=self.host,
        )
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class Remote:
    def __init__(self, p):
        self.p = p
        self.client = None
        self.sftp = None
        self.smb = None
        self.cache = {}
        if p.kind == "smb":
            import smbclient

            if "/" in p.share or "\\" in p.share:
                raise GenerationError("SMB共有名にはサーバー名やパスを含めません")
            self.smb = smbclient
            kwargs = {
                "port": p.port or 445,
                "connection_timeout": p.timeout_seconds,
                "encrypt": p.smb_encrypt,
                "connection_cache": self.cache,
                "auth_protocol": "kerberos" if p.file_auth == "kerberos" else "ntlm",
            }
            if p.file_auth != "kerberos":
                kwargs.update(
                    username=(p.domain + "\\" if p.domain else "") + p.username,
                    password=secret(p, "password"),
                )
            self.client = smbclient.register_session(p.host, **kwargs)
            self.root = "\\\\" + p.host + "\\" + p.share
            if p.directory:
                self.root += "\\" + p.directory.replace("/", "\\").strip("\\")
        elif p.kind == "ftp":
            if p.ssl_mode == "off":
                client = ftplib.FTP(timeout=p.timeout_seconds, encoding=p.ftp_encoding)
            else:
                client = (
                    ImplicitFTP if p.ftp_tls_mode == "implicit" else ftplib.FTP_TLS
                )(
                    context=tls_context(p),
                    timeout=p.timeout_seconds,
                    encoding=p.ftp_encoding,
                )
            self.client = client
            try:
                client.connect(
                    p.host,
                    p.port
                    or (
                        990
                        if p.ssl_mode != "off" and p.ftp_tls_mode == "implicit"
                        else 21
                    ),
                )
                client.login(
                    p.username if p.file_auth != "anonymous" else "anonymous",
                    secret(p, "password")
                    if p.file_auth != "anonymous"
                    else "anonymous@",
                )
                if p.ssl_mode != "off":
                    client.prot_p()
                client.set_pasv(p.ftp_passive)
                self.root = p.directory or "."
                client.cwd(self.root)
                self.root = client.pwd()
            except Exception:
                self.close()
                raise
        elif p.kind == "sftp":
            import paramiko

            client = paramiko.SSHClient()
            self.client = client
            if p.ssh_known_hosts:
                client.load_host_keys(connection_file(p.ssh_known_hosts))
            else:
                client.load_system_host_keys()
            if p.ssh_host_key_policy == "accept_any":
                client.set_missing_host_key_policy(paramiko.WarningPolicy())
            elif p.ssh_host_key_policy == "sha256":
                if not p.ssh_host_key_sha256:
                    raise GenerationError(
                        "SSHホスト鍵のSHA256フィンガープリントが必要です"
                    )

                class Pinned(paramiko.MissingHostKeyPolicy):
                    def missing_host_key(self, client, hostname, key):
                        actual = "SHA256:" + base64.b64encode(
                            hashlib.sha256(key.asbytes()).digest()
                        ).decode().rstrip("=")
                        if actual != p.ssh_host_key_sha256:
                            raise GenerationError(
                                "SSHホスト鍵が指定したフィンガープリントと一致しません"
                            )

                client.set_missing_host_key_policy(Pinned())
            kwargs = {
                "hostname": p.host,
                "port": p.port or 22,
                "username": p.username,
                "timeout": p.timeout_seconds,
                "banner_timeout": p.timeout_seconds,
                "auth_timeout": p.timeout_seconds,
                "allow_agent": False,
                "look_for_keys": False,
            }
            if p.file_auth == "ssh_key":
                if p.ssh_private_key:
                    key = None
                    for kind in [
                        paramiko.Ed25519Key,
                        paramiko.RSAKey,
                        paramiko.ECDSAKey,
                    ]:
                        try:
                            key = kind.from_private_key(
                                io.StringIO(secret(p, "ssh_private_key")),
                                password=secret(p, "ssh_key_password") or None,
                            )
                            break
                        except (paramiko.SSHException, ValueError):
                            continue
                    if key is None:
                        raise GenerationError(
                            "SSH秘密鍵の形式またはパスフレーズを確認してください"
                        )
                    kwargs["pkey"] = key
                elif p.ssh_key_file:
                    kwargs.update(
                        key_filename=connection_file(p.ssh_key_file),
                        passphrase=secret(p, "ssh_key_password") or None,
                    )
                else:
                    raise GenerationError("SSH秘密鍵または鍵ファイルが必要です")
            else:
                kwargs["password"] = secret(p, "password")
            try:
                client.connect(**kwargs)
                self.sftp = client.open_sftp()
                self.sftp.get_channel().settimeout(p.timeout_seconds)
                self.root = self.sftp.normalize(p.directory or ".")
            except Exception:
                self.close()
                raise
        else:
            raise GenerationError("SMB / FTP / SFTP共有を選択してください")

    def path(self, relative):
        if (
            PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or "\\" in relative
        ):
            raise GenerationError("共有内の相対パスが不正です")
        return (
            self.root + "\\" + relative.replace("/", "\\")
            if self.p.kind == "smb"
            else posixpath.join(self.root, relative)
        )

    def test(self):
        if self.p.kind == "smb":
            self.smb.stat(
                self.root, port=self.p.port or 445, connection_cache=self.cache
            )
        elif self.p.kind == "sftp":
            self.sftp.stat(self.root)
        else:
            self.client.voidcmd("NOOP")
        return {
            "status": "connected",
            "kind": self.p.kind,
            "directory": self.p.directory or "(home)",
            "writes": False,
        }

    def write(self, relative, data):
        target = self.path(relative)
        temporary = target + "." + uuid.uuid4().hex + ".part"
        if self.p.kind == "smb":
            self.smb.makedirs(
                target.rsplit("\\", 1)[0],
                exist_ok=True,
                port=self.p.port or 445,
                connection_cache=self.cache,
            )
            if self.smb.path.exists(
                target, port=self.p.port or 445, connection_cache=self.cache
            ):
                raise FileExistsError("既存ファイルは上書きしません")
            try:
                with self.smb.open_file(
                    temporary,
                    mode="xb",
                    port=self.p.port or 445,
                    connection_cache=self.cache,
                ) as f:
                    f.write(data)
                    f.flush()
                self.smb.rename(
                    temporary,
                    target,
                    port=self.p.port or 445,
                    connection_cache=self.cache,
                )
            except Exception:
                try:
                    self.smb.remove(
                        temporary, port=self.p.port or 445, connection_cache=self.cache
                    )
                except OSError:
                    pass
                raise
        elif self.p.kind == "sftp":
            parent = posixpath.dirname(target)
            parts = parent.split("/")
            current = "/" if parent.startswith("/") else ""
            for part in parts:
                if not part:
                    continue
                current = posixpath.join(current, part)
                try:
                    self.sftp.stat(current)
                except FileNotFoundError:
                    self.sftp.mkdir(current)
            try:
                self.sftp.stat(target)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("既存ファイルは上書きしません")
            try:
                with self.sftp.open(temporary, "wx") as f:
                    f.write(data)
                    f.flush()
                self.sftp.rename(temporary, target)
            except Exception:
                try:
                    self.sftp.remove(temporary)
                except OSError:
                    pass
                raise
        else:
            parent = posixpath.dirname(target)
            current = "/" if parent.startswith("/") else ""
            for part in parent.split("/"):
                if not part:
                    continue
                current = posixpath.join(current, part)
                try:
                    self.client.mkd(current)
                except ftplib.error_perm:
                    original = self.client.pwd()
                    self.client.cwd(current)
                    self.client.cwd(original)
            self.client.voidcmd("TYPE I")
            try:
                self.client.size(target)
            except ftplib.error_perm:
                pass
            else:
                raise FileExistsError("既存ファイルは上書きしません")
            try:
                self.client.storbinary("STOR " + temporary, io.BytesIO(data))
                self.client.rename(temporary, target)
            except Exception:
                try:
                    self.client.delete(temporary)
                except ftplib.error_perm:
                    pass
                raise

    def read(self, relative, limit=64 * 1024 * 1024):
        target = self.path(relative)
        if self.p.kind == "smb":
            with self.smb.open_file(
                target, mode="rb", port=self.p.port or 445, connection_cache=self.cache
            ) as f:
                return f.read(limit + 1)
        if self.p.kind == "sftp":
            with self.sftp.open(target, "rb") as f:
                return f.read(limit + 1)
        output = io.BytesIO()

        def collect(data):
            if output.tell() + len(data) > limit:
                raise GenerationError("画像が大きすぎます")
            output.write(data)

        self.client.retrbinary("RETR " + target, collect)
        return output.getvalue()

    def close(self):
        if self.sftp:
            self.sftp.close()
        if self.p.kind == "smb" and self.smb:
            self.smb.delete_session(
                self.p.host, port=self.p.port or 445, connection_cache=self.cache
            )
        elif self.client:
            self.client.close()

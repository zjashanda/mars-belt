#!/usr/bin/env python3
"""
send-email skill - 简化版
只负责发送邮件，接收已准备好的 HTML 内容 + zip 附件。
默认优先使用同目录下的 mail_summary.html 作为正文；
如果调用方未显式传 zip，且同目录下存在 result.zip，则自动附带。
用法: python3 send_email.py <收件人> <邮件主题> <html路径> [zip路径]
"""

import smtplib
import os
import sys
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

SKILL_ROOT = Path(__file__).resolve().parents[2]
TOOLS_MD = SKILL_ROOT / "TOOLS.md"


def load_local_config() -> dict[str, str]:
    data: dict[str, str] = {}
    if not TOOLS_MD.exists():
        return data
    for line in TOOLS_MD.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def resolve_mail_settings() -> tuple[str, str, str, int]:
    local = load_local_config()
    from_addr = os.environ.get("MAIL_FROM_ADDR") or local.get("MAIL_FROM_ADDR") or ""
    password = os.environ.get("MAIL_PASSWORD") or local.get("MAIL_PASSWORD") or ""
    smtp_server = os.environ.get("MAIL_SMTP_SERVER") or local.get("MAIL_SMTP_SERVER") or ""
    smtp_port_text = os.environ.get("MAIL_SMTP_PORT") or local.get("MAIL_SMTP_PORT") or "465"

    if not from_addr or not password or not smtp_server:
        raise RuntimeError("Missing mail config. Set MAIL_FROM_ADDR / MAIL_PASSWORD / MAIL_SMTP_SERVER / MAIL_SMTP_PORT in env or TOOLS.md.")

    try:
        smtp_port = int(smtp_port_text)
    except ValueError as exc:
        raise RuntimeError(f"Invalid MAIL_SMTP_PORT: {smtp_port_text}") from exc

    return from_addr, password, smtp_server, smtp_port


def resolve_body_html(html_path: str) -> Path:
    requested = Path(html_path)
    compact = requested.with_name("mail_summary.html")
    if compact.exists():
        print(f"[send-email] 使用简版邮件正文: {compact}")
        return compact
    return requested


def resolve_zip_path(body_path: Path, zip_path: str | None) -> Path | None:
    if zip_path:
        candidate = Path(zip_path)
        if not candidate.exists():
            print(f"[send-email] 附件不存在: {candidate}")
            return None
        return candidate
    auto = body_path.with_name("result.zip")
    if auto.exists():
        print(f"[send-email] 自动附加同目录结果包: {auto}")
        return auto
    return None


def send_email(to_addr: str, subject: str, html_path: str, zip_path: str = None):
    """发送邮件"""
    from_addr, password, smtp_server, smtp_port = resolve_mail_settings()
    body_path = resolve_body_html(html_path)
    if not body_path.exists():
        print(f"[send-email] HTML 文件不存在: {body_path}")
        return False
    attachment_path = resolve_zip_path(body_path, zip_path)

    with open(body_path, 'r', encoding='utf-8') as f:
        html_body = f.read()
    
    # 创建邮件
    msg = MIMEMultipart('alternative')
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    
    # 添加 zip 附件
    if attachment_path:
        with open(attachment_path, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = os.path.basename(attachment_path)
            part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
            msg.attach(part)
        print(f"[send-email] 已添加附件: {filename}")
    
    # 发送邮件
    with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
        server.login(from_addr, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
    
    print(f"[send-email] ✅ 邮件已发送至: {to_addr}")
    return True


if __name__ == "__main__":
    # 用法: python3 send_email.py <收件人> <邮件主题> <html路径> <zip路径>
    if len(sys.argv) < 4:
        print("用法: python3 send_email.py <收件人> <邮件主题> <html路径> [zip路径]")
        sys.exit(1)
    
    to_addr = sys.argv[1]
    subject = sys.argv[2]
    html_path = sys.argv[3]
    zip_path = sys.argv[4] if len(sys.argv) > 4 else None
    
    success = send_email(to_addr, subject, html_path, zip_path)
    sys.exit(0 if success else 1)

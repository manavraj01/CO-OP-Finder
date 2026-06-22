#!/usr/bin/env python3
"""
Co-op Monitor — Google OAuth with .env credentials
Just copy this file to your folder and run it!
"""

import json, os, re, sys, urllib.request, base64, datetime

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Install: pip install python-dotenv")
    sys.exit(1)

# Google OAuth
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
except ImportError as e:
    print(f"ImportError: {e}")
    print("Install: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    sys.exit(1)

# Config
REPOS_FILE = "repos.txt"
SNAPSHOT_FILE = ".coops_snapshot.json"
TOKEN_FILE = "token.json"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# Regex
LINK_RE = re.compile(r"\]\(\s*<?(https?://[^)>\s]+)>?\s*\)")
COMMENT_RE = re.compile(r"<!--.*?-->")
TAG_RE = re.compile(r"<[^>]+>")

# Winter 2027 filter — matches term indicators in role/details text
WINTER_2027_RE = re.compile(
    r"winter\s*2027|w[-_]?2027|2027\s*winter|"
    r"jan(uary)?\s*2027|feb(ruary)?\s*2027|mar(ch)?\s*2027|apr(il)?\s*2027",
    re.IGNORECASE,
)
OTHER_TERM_RE = re.compile(
    r"(summer|fall|spring|winter)\s*20(?!27)\d\d",
    re.IGNORECASE,
)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except:
        return ""


def normalize_repo(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = re.search(r"github\.com[/:]([^/\s]+/[^/\s]+)", line)
    slug = (m.group(1) if m else line).rstrip("/")
    slug = re.sub(r"\.git$", "", slug)
    return slug


def list_markdown_files(slug):
    url = f"https://api.github.com/repos/{slug}/contents/"
    try:
        data = json.loads(http_get(url))
        return [(item["name"], item["download_url"]) 
                for item in data if item.get("type") == "file" and item["name"].lower().endswith(".md")]
    except:
        return []


def split_row(line):
    t = line.strip()
    if t.startswith("|"): t = t[1:]
    if t.endswith("|"): t = t[:-1]
    return [c.strip() for c in t.split("|")]


def clean_text(s):
    s = COMMENT_RE.sub("", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = TAG_RE.sub("", s)
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_col(headers, *needles):
    for i, h in enumerate(headers):
        for n in needles:
            if n in h: return i
    return None


def extract_url(cell):
    matches = LINK_RE.findall(cell)
    if matches: return matches[-1].strip()
    m = re.search(r"<?(https?://[^)>\s]+)>?", cell)
    return m.group(1) if m else ""


def parse_tables(md, source):
    rows, block = [], []

    def flush(block):
        if len(block) < 2: return
        headers = [c.lower() for c in split_row(block[0])]
        i_company = find_col(headers, "company", "employer")
        i_title = find_col(headers, "title")
        i_role = find_col(headers, "role", "position")
        i_loc = find_col(headers, "location", "city")
        i_apply = find_col(headers, "apply", "application", "link")
        detail_idx = [i for i, h in enumerate(headers) if any(k in h for k in ("detail", "term", "season", "date", "note", "info"))]
        name_idx = i_title if i_title is not None else i_role
        if name_idx is None or i_apply is None: return
        last_company = ""
        for line in block[2:]:
            cells = split_row(line)
            if len(cells) <= max(filter(lambda x: x is not None, [name_idx, i_apply, i_company or 0, i_loc or 0])): continue
            url = extract_url(cells[i_apply]) if i_apply < len(cells) else ""
            name = clean_text(cells[name_idx]) if name_idx < len(cells) else ""
            if not url or not name: continue
            company = clean_text(cells[i_company]) if (i_company is not None and i_company < len(cells)) else ""
            if company in ("", "↳", "â³", "↳"): company = last_company
            else: last_company = company
            location = clean_text(cells[i_loc]) if (i_loc is not None and i_loc < len(cells)) else "—"
            details = " ".join(clean_text(cells[d]) for d in detail_idx if d < len(cells))
            rows.append({
                "company": company or "—",
                "role": name,
                "location": location or "—",
                "url": url,
                "source": source,
                "details": details,
            })

    for line in md.split("\n"):
        if line.strip().startswith("|"):
            block.append(line)
        else:
            flush(block); block = []
    flush(block)
    return rows


def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
                print("❌ Missing in .env:")
                print("   GOOGLE_CLIENT_ID=...")
                print("   GOOGLE_CLIENT_SECRET=...")
                sys.exit(1)
            
            client_config = {
                "installed": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            # access_type='offline' + prompt='consent' ensures Google always returns
            # a refresh_token, which is required for unattended runs in GitHub Actions.
            creds = flow.run_local_server(port=8080, access_type='offline', prompt='consent')
        
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    
    return build("gmail", "v1", credentials=creds)


def send_email(gmail_service, to_email, subject, text_body, html_body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        
        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail_service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
        print(f"✓ Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"✗ Email failed: {e}")
        return False


def fetch_all():
    if not os.path.exists(REPOS_FILE):
        print("❌ repos.txt not found")
        return []
    
    with open(REPOS_FILE) as f:
        repos = [r for r in (normalize_repo(l) for l in f) if r]
    
    all_rows = []
    for slug in repos:
        try:
            files = list_markdown_files(slug)
            for name, dl in files:
                try:
                    md = http_get(dl)
                    all_rows += parse_tables(md, slug)
                except:
                    pass
        except:
            pass
    
    # Dedupe
    seen, unique = set(), []
    for r in all_rows:
        if r["url"].rstrip("/") not in seen:
            seen.add(r["url"].rstrip("/"))
            unique.append(r)
    return unique


def main():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Scanning repos...")
    
    current = fetch_all()
    print(f"→ Found {len(current)} total postings")

    # Keep only Winter 2027 positions
    def is_winter_2027(row: dict) -> bool:
        text = f"{row.get('role', '')} {row.get('details', '')}".lower()
        if WINTER_2027_RE.search(text):
            return True
        if OTHER_TERM_RE.search(text):
            return False
        return True  # no term info found — include to avoid missing postings

    current = [r for r in current if is_winter_2027(r)]
    print(f"→ {len(current)} Winter 2027 postings after filter")
    
    previous = []
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE) as f:
                previous = json.load(f)
        except:
            pass
    
    prev_urls = {r["url"].rstrip("/") for r in previous}
    new = [r for r in current if r["url"].rstrip("/") not in prev_urls]
    
    if new:
        print(f"✨ {len(new)} NEW postings!")
        for r in new:
            print(f"  • {r['company']} — {r['role']}")
        
        if ALERT_EMAIL:
            text = f"Found {len(new)} new co-op postings:\n\n"
            for r in new:
                text += f"• {r['company']} — {r['role']}\n  {r['location']} | {r['source']}\n  {r['url']}\n\n"
            
            html = f"""<html><body style="font-family: sans-serif; background: #f5f5f5;">
<div style="max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px;">
<h2 style="color: #e2483d;">🚀 {len(new)} New Co-op Postings</h2>
<p>{datetime.datetime.now().strftime('%B %d, %Y at %H:%M')}</p>
"""
            for r in new:
                html += f"""<div style="border-left: 4px solid #6aa6e0; padding: 12px; margin: 12px 0; background: #f9f9f9;">
  <strong>{r['company']}</strong><br/>
  <span style="color: #666;">{r['role']}</span><br/>
  <small style="color: #999;">{r['location']} • {r['source']}</small><br/>
  <a href="{r['url']}" style="color: #6aa6e0;">Apply →</a>
</div>"""
            html += "</div></body></html>"
            
            gmail = get_gmail_service()
            send_email(gmail, ALERT_EMAIL, f"🚀 {len(new)} New Co-op Postings!", text, html)
        else:
            print("⚠️  Set ALERT_EMAIL in .env to get emails")
    else:
        print("✓ No new postings")
    
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(current, f)
    print("✓ Done\n")


if __name__ == "__main__":
    main()
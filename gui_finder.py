#!/usr/bin/env python3
"""
Winter 2027 Co-op Finder — GUI edition.
No CLI args. Just dropdowns, a button, and results.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os, json, re, sys, urllib.request, urllib.parse, html, webbrowser, datetime
import threading

REPOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repos.txt")
REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coops_report.html")
JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coops.json")


# ============================================================================
# Parsing (same as find_coops.py)
# ============================================================================
LINK_RE = re.compile(r"\]\(\s*<?(https?://[^)>\s]+)>?\s*\)")
COMMENT_RE = re.compile(r"<!--.*?-->")
TAG_RE = re.compile(r"<[^>]+>")


def http_get(url, accept=None):
    req = urllib.request.Request(url, headers={"User-Agent": "coop-finder/1.0"})
    if accept:
        req.add_header("Accept", accept)
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def normalize_repo(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = re.search(r"github\.com[/:]([^/\s]+/[^/\s]+)", line)
    slug = (m.group(1) if m else line).rstrip("/")
    slug = re.sub(r"\.git$", "", slug)
    return slug


def list_markdown_files(slug):
    url = "https://api.github.com/repos/%s/contents/" % slug
    data = json.loads(http_get(url, accept="application/vnd.github+json"))
    out = []
    for item in data:
        if item.get("type") == "file" and item["name"].lower().endswith(".md"):
            out.append((item["name"], item["download_url"]))
    return out


def split_row(line):
    t = line.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|"):
        t = t[:-1]
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
            if n in h:
                return i
    return None


def extract_url(cell):
    matches = LINK_RE.findall(cell)
    if matches:
        return matches[-1].strip()
    m = re.search(r"<?(https?://[^)>\s]+)>?", cell)
    return m.group(1) if m else ""


def parse_tables(md, source):
    rows, block = [], []

    def flush(block):
        if len(block) < 2:
            return
        headers = [c.lower() for c in split_row(block[0])]
        i_company = find_col(headers, "company", "employer")
        i_title = find_col(headers, "title")
        i_role = find_col(headers, "role", "position")
        i_loc = find_col(headers, "location", "city")
        i_apply = find_col(headers, "apply", "application", "link")
        detail_idx = [i for i, h in enumerate(headers)
                      if any(k in h for k in ("detail", "term", "season", "date", "note", "info"))]
        name_idx = i_title if i_title is not None else i_role
        if name_idx is None or i_apply is None:
            return
        last_company = ""
        for line in block[2:]:
            cells = split_row(line)
            if len(cells) <= max(filter(lambda x: x is not None,
                                        [name_idx, i_apply, i_company or 0, i_loc or 0])):
                continue
            url = extract_url(cells[i_apply]) if i_apply < len(cells) else ""
            name = clean_text(cells[name_idx]) if name_idx < len(cells) else ""
            if not url or not name:
                continue
            company = clean_text(cells[i_company]) if (i_company is not None and i_company < len(cells)) else ""
            if company in ("", "↳", "â³", "↳"):
                company = last_company
            else:
                last_company = company
            location = clean_text(cells[i_loc]) if (i_loc is not None and i_loc < len(cells)) else "—"
            details = " ".join(clean_text(cells[d]) for d in detail_idx if d < len(cells))
            role_desc = clean_text(cells[i_role]) if (i_role is not None and i_role < len(cells) and i_role != name_idx) else ""
            try:
                decoded = urllib.parse.unquote(url)
            except Exception:
                decoded = url
            hay = " ".join([name, role_desc, details, decoded]).lower().replace("-", " ").replace("_", " ")
            rows.append({
                "company": company or "—",
                "role": name,
                "location": location or "—",
                "url": url,
                "details": details,
                "source": source,
                "_hay": hay,
            })

    for line in md.split("\n"):
        if line.strip().startswith("|"):
            block.append(line)
        else:
            flush(block); block = []
    flush(block)
    return rows


# ============================================================================
# Filtering
# ============================================================================
def match_term(hay, terms):
    if not terms:
        return True
    return any(t in hay for t in terms)


def match_type(hay, kind):
    if kind == "any":
        return True
    is_coop = bool(re.search(r"co ?op|cooperative", hay))
    if kind == "coop":
        return is_coop
    return is_coop or bool(re.search(r"intern|stage|stagiaire|student", hay))


def is_winter27(hay):
    return "winter 2027" in hay


# ============================================================================
# GUI
# ============================================================================
class CoopFinderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Winter 2027+ Co-op Finder")
        self.root.geometry("900x700")
        self.root.configure(bg="#0f1115")
        
        style = ttk.Style()
        style.theme_use("clam")
        
        # Custom colors
        style.configure("TFrame", background="#0f1115")
        style.configure("TLabel", background="#0f1115", foreground="#e7e9ee")
        style.configure("TButton", font=("TkDefaultFont", 10))
        style.map("TButton",
                  foreground=[("pressed", "#fff"), ("active", "#fff")],
                  background=[("pressed", "#b23a32"), ("active", "#e2483d")])
        style.configure("Scan.TButton", font=("TkDefaultFont", 11, "bold"))
        
        # Main frame
        main = ttk.Frame(root, padding=20)
        main.pack(fill="both", expand=True)
        
        # Header
        title = ttk.Label(main, text="Winter 2027+ Co-op Finder", 
                         font=("TkDefaultFont", 18, "bold"), foreground="#e2483d")
        title.pack(anchor="w", pady=(0, 20))
        
        # Controls frame
        ctrl = ttk.Frame(main)
        ctrl.pack(fill="x", pady=(0, 16))
        
        # Dropdowns
        row1 = ttk.Frame(ctrl)
        row1.pack(fill="x", pady=(0, 12))
        
        ttk.Label(row1, text="Term:").pack(side="left", padx=(0, 8))
        self.term_var = tk.StringVar(value="winter 2027")
        term_dd = ttk.Combobox(row1, textvariable=self.term_var, state="readonly",
                              values=["winter 2027", "fall 2026", "summer 2027", "spring 2027", "any"], width=18)
        term_dd.pack(side="left", padx=(0, 24))
        
        ttk.Label(row1, text="Type:").pack(side="left", padx=(0, 8))
        self.type_var = tk.StringVar(value="coop")
        type_dd = ttk.Combobox(row1, textvariable=self.type_var, state="readonly",
                              values=["coop", "both", "any"], width=18)
        type_dd.pack(side="left")
        
        # Scan button
        row2 = ttk.Frame(ctrl)
        row2.pack(fill="x")
        
        self.scan_btn = ttk.Button(row2, text="🔍 Scan Repos", command=self.scan, style="Scan.TButton")
        self.scan_btn.pack(side="left", padx=(0, 12))
        
        self.status_label = ttk.Label(row2, text="Ready", foreground="#8b93a1")
        self.status_label.pack(side="left")
        
        # Results
        ttk.Label(main, text="Results", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", pady=(16, 8))
        
        self.results = scrolledtext.ScrolledText(main, height=20, width=100,
                                                 bg="#171a21", fg="#e7e9ee",
                                                 font=("Courier", 10), wrap="word")
        self.results.pack(fill="both", expand=True, pady=(0, 12))
        
        # Buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x")
        
        ttk.Button(btn_frame, text="📂 Open HTML Report", command=self.open_report).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="📋 Copy Apply Links", command=self.copy_links).pack(side="left")
        
        self.last_results = []

    def scan(self):
        self.scan_btn.config(state="disabled")
        self.status_label.config(text="Scanning...", foreground="#6aa6e0")
        self.results.delete("1.0", "end")
        
        # Run scan in background thread
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        try:
            if not os.path.exists(REPOS_FILE):
                self._log("❌ No repos.txt found. Create one with repos like:\n"
                         "negarprh/Canadian-Tech-Internships-2026\n"
                         "hanzili/canada_sde_intern_position")
                return

            with open(REPOS_FILE, encoding="utf-8") as f:
                repos = [r for r in (normalize_repo(l) for l in f) if r]
            
            if not repos:
                self._log("❌ repos.txt is empty. Add some repos!")
                return

            self._log(f"→ Scanning {len(repos)} repo(s)...\n")
            
            all_rows = []
            for slug in repos:
                try:
                    files = list_markdown_files(slug)
                    repo_rows = []
                    for name, dl in files:
                        try:
                            md = http_get(dl)
                            repo_rows += parse_tables(md, slug)
                        except Exception as e:
                            pass
                    self._log(f"✓ {slug} — {len(repo_rows)} postings")
                    all_rows += repo_rows
                except Exception as e:
                    self._log(f"✗ {slug} — couldn't fetch")

            # Filter
            term_val = self.term_var.get()
            type_val = self.type_var.get()
            
            terms = [] if term_val == "any" else [term_val.lower()]
            
            kept = []
            for r in all_rows:
                if not match_term(r["_hay"], terms):
                    continue
                if not match_type(r["_hay"], type_val):
                    continue
                kept.append(r)

            # Dedupe
            seen, unique = set(), []
            for r in kept:
                if r["url"].rstrip("/") not in seen:
                    seen.add(r["url"].rstrip("/"))
                    r.pop("_hay", None)
                    unique.append(r)

            self.last_results = unique
            
            # Show results
            self._log(f"\n✨ {len(unique)} match(es) found!\n")
            for i, r in enumerate(unique[:30], 1):
                self._log(f"{i}. {r['company']} — {r['role']}")
                self._log(f"   Location: {r['location']} | Source: {r['source']}\n")
            
            if len(unique) > 30:
                self._log(f"... and {len(unique) - 30} more (see HTML report)")
            
            # Save JSON & HTML
            with open(JSON_FILE, "w", encoding="utf-8") as f:
                json.dump(unique, f, indent=2, ensure_ascii=False)
            
            self._build_report(unique, term_val, type_val)
            
        except Exception as e:
            self._log(f"❌ Error: {str(e)}")
        finally:
            self.scan_btn.config(state="normal")
            self.status_label.config(text="Done!", foreground="#46b885")

    def _log(self, text):
        self.results.insert("end", text)
        self.results.see("end")
        self.root.update()

    def _build_report(self, rows, term_val, type_val):
        """Build the HTML report (simplified version)"""
        payload = json.dumps(rows).replace("</", "<\\/")
        html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<title>Co-op Finder Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1115; color: #e7e9ee; margin: 0; padding: 40px 20px; }}
.wrap {{ max-width: 900px; margin: 0 auto; }}
h1 {{ color: #e2483d; margin: 0 0 10px; }}
.card {{ background: #171a21; border: 1px solid #272c37; border-radius: 8px; padding: 16px; margin: 12px 0; }}
.card-company {{ color: #6aa6e0; font-size: 11px; text-transform: uppercase; margin-bottom: 6px; }}
.card-role {{ font-size: 16px; font-weight: 600; margin-bottom: 8px; }}
.meta {{ display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; }}
.tag {{ background: #1d212a; padding: 4px 10px; border-radius: 12px; border: 1px solid #272c37; }}
.tag.near {{ color: #46b885; }}
a {{ color: #6aa6e0; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="wrap">
<h1>Winter 2027+ Co-ops</h1>
<p>Filtered: {term_val} · {type_val}</p>
<div id="cards"></div>
</div>
<script>
const DATA = {payload};
document.getElementById("cards").innerHTML = DATA.map(r => `
  <div class="card">
    <div class="card-company">${{r.company}}</div>
    <div class="card-role">${{r.role}}</div>
    <div class="meta">
      <span class="tag">${{r.location}}</span>
      <span class="tag">${{r.source}}</span>
    </div>
    <p><a href="${{r.url}}" target="_blank">Apply →</a></p>
  </div>
`).join("");
</script>
</body>
</html>"""
        
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(html_content)

    def open_report(self):
        if os.path.exists(REPORT_FILE):
            webbrowser.open("file://" + os.path.abspath(REPORT_FILE))
        else:
            messagebox.showinfo("Info", "Scan first to generate report")

    def copy_links(self):
        if not self.last_results:
            messagebox.showinfo("Info", "Scan first to get results")
            return
        
        links = "\n\n".join(f"{r['company']} — {r['role']}\n{r['url']}" 
                           for r in self.last_results)
        self.root.clipboard_clear()
        self.root.clipboard_append(links)
        messagebox.showinfo("Copied", f"{len(self.last_results)} apply links copied to clipboard")


if __name__ == "__main__":
    root = tk.Tk()
    app = CoopFinderGUI(root)
    root.mainloop()
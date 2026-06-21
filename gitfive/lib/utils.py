import httpx
import Levenshtein
from bs4 import BeautifulSoup
from rich import print as rprint
from rich.console import Console
import imagehash
from PIL import Image
from unidecode import unidecode
import hashlib
import base64
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict

import os
import re
import stat
import socket
from pathlib import Path
from dateutil.relativedelta import *
from typing import *
from io import BytesIO
import string
from packaging.version import parse as parse_version
import json

import gitfive.config as config
from gitfive.lib.objects import GitfiveRunner
from gitfive.lib.banner import banner
from gitfive import version as current_version


def is_local_domain(domain: str):
    return not "." in domain or any([domain.endswith(f".{tld}") for tld in config.local_tlds])

def get_image_hash(img: Image):
    """Return the hash of the pixels of an image"""
    hash = str(imagehash.average_hash(img))
    return hash

def fetch_img(url: str):
    """Download an image and return a PIL's Image object."""
    req = httpx.get(url)
    img = Image.open(BytesIO(req.content))
    return img

def extract_domain(url: str, sub_level: int=0):
    if url.startswith('http'):
        return '.'.join(url.split('/')[2].split('.')[-(sub_level+2):])
    return '.'.join(url.split('/')[0].split('.')[-(sub_level+2):])

def detect_custom_domain(link: str):
    link = link.strip('/')
    domains = []
    if "." in link and (link.count('/') >= 2 or '/' not in link):
        nb_of_dots = link.count('.')
        if nb_of_dots > 3: # Avoiding domains with too much subdomains,
                           # so we only extract longest and shortest domain
            domains.append(extract_domain(link, 0))
            domains.append(extract_domain(link, nb_of_dots-1))
        else:
            for sub_level in range(nb_of_dots):
                domain = extract_domain(link, sub_level)
                if not domain.startswith("www.") and not domain.endswith("github.io"):
                    domains.append(domain)
    return domains

def is_diff_low(string1: str, string2: str, limit: int=40):
    """Calculate difference pourcentage between
    two strings with Levenshtein algorithm"""

    diff = Levenshtein.distance(string1, string2)
    first_len = len(string1)
    pourcentage = int(diff/first_len*100)

    if pourcentage <= limit:
        return True
    return False

def is_repo_empty(body: BeautifulSoup):
    if body.h3 and any(['this repository is empty' in x.text.lower() for x in body.find_all("h3")]):
        return True
    return False

def get_link_location(domain: str):
    """If the HTTP redirects to HTTPS, it returns the HTTPS link"""
    http_link = f"http://{domain}"
    https_link = f"https://{domain}"
    req = httpx.head(http_link) # We use HEAD method to optimize speed and not fetching the body
    final_url = req.url.__str__()
    if final_url.startswith((http_link, https_link)):
        return final_url
    else:
        return http_link

def is_ghpages_hosted(domain: str):
    try:
        ip = socket.gethostbyname(domain)
    except Exception:
        return False
    else:
        if ip in config.ghpages_servers:
            return True
        return False

def change_permissions(path: Path|str):
    for root, dirs, files in os.walk(path):  
        for dir in dirs:
            os.chmod(Path(root) / Path(dir), stat.S_IRWXU)
        for file in files:
            os.chmod(Path(root) / Path(file), stat.S_IRWXU)

def show_banner():
    rprint(banner)

async def get_commits_count(runner: GitfiveRunner, repo_url: str="", raw_body: str=""):
    if not raw_body:
        req = await runner.as_client.get(repo_url)
        raw_body = req.text
    # Slightly modified this line to find the correct <span> containing the commit count
    matches = re.findall(r'"commitCount":"(.*?)"', raw_body)
    if not matches:
        return False, 0
    nb_commits_str = matches[0].replace(",", "")

    if nb_commits_str == "∞":
        return True, 50000 # Temporary limit, because GitHub hasn't liked my 70k commits
    nb_commits = int(nb_commits_str)
    return True, nb_commits

def chunks(lst: List[any], n: int):
    """
        Yield successive n-sized chunks from list.
    """
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def humanize_list(array: List[any]):
    """
        Transforms a list to a human sentence.
        Ex : ["reader", "writer", "owner"] -> "reader, writer and owner".
    """
    if len(array) <= 1:
        return ''.join(array)

    final = ""
    for nb, item in enumerate(array):
        if nb == 0:
            final += f"{item}"
        elif nb+1 < len(array):
            final += f", {item}"
        else:
            final += f" and {item}"
    return final

def sanatize(text: str) -> str:
    deaccented = ""
    try:
        deaccented = unidecode(text, "utf-8")
    except Exception:
        pre_sanatize = ''.join([*filter(lambda x:x.isalpha() or x in "-. ", text)]) # kudos to @n1nj4sec
        deaccented = unidecode(pre_sanatize, "utf-8")
    return ''.join([*filter(lambda x:x.lower() in string.ascii_lowercase+" ", deaccented)])

def get_gists_stats(runner: GitfiveRunner):
    req = httpx.get(f"https://gist.github.com/{runner.target.username}/starred")
    body = BeautifulSoup(req.text, 'html.parser')
    stats = [int(x.text) for x in body.select('span.Counter')]
    return {"gists": stats[0], "starred": stats[1]}

async def get_ssh_keys(runner: GitfiveRunner):
    req = await runner.as_client.get(f"https://github.com/{runner.target.username}.keys")
    lines = req.text.strip()
    if lines:
        runner.target.ssh_keys.extend(lines.split("\n"))

def delete_tmp_dir():
    from shutil import rmtree
    cwd_path = Path().home()
    gitfive_folder = cwd_path / ".malfrats/gitfive"
    gitfive_folder.mkdir(parents=True, exist_ok=True)
    
    target_user_folder: Path = gitfive_folder / ".tmp"

    change_permissions(target_user_folder)
    rmtree(target_user_folder)

def unicode_patch(txt: str):
    bad_chars = {
        "é": "e",
        "è": "e",
        "ç": "c",
        "à": "a"
    }
    return txt.replace(''.join([*bad_chars.keys()]), ''.join([*bad_chars.values()]))

def safe_print(txt: str):
    """
        Escape the bad characters to avoid ANSI injections.
        Also works for Rich printers.
    """
    return txt.encode("unicode_escape").decode().replace('[', '\\[')

def show_version():
    new_version, new_metadata = check_new_version()
    co = Console(highlight=False)
    co.print(f"> GitFive {current_version.metadata.get('version', '')} ({current_version.metadata.get('name', '')}) <".center(62), style="bold")
    print()
    if new_version:
        co.print(f"🥳 New version {new_metadata.get('version', '')} ({new_metadata.get('name', '')}) is available !", style="bold red")
        co.print(f"🤗 Run 'pipx upgrade gitfive' to update.", style="bold light_pink3")
    else:
        co.print("🎉 You are up to date !", style="light_pink3")


def check_new_version() -> tuple[bool, dict[str, str]]:
    """
        Checks if there is a new version of GitFive available.
    """
    req = httpx.get("https://raw.githubusercontent.com/mxrch/GitFive/master/gitfive/version.py")
    if req.status_code != 200:
        return False, {}

    raw = req.text.strip().removeprefix("metadata = ")
    data = json.loads(raw)
    new_version = data.get("version", "")
    new_name = data.get("name", "")

    if parse_version(new_version) > parse_version(current_version.metadata.get("version", "")):
        return True, {"version": new_version, "name": new_name}
    return False, {}


def extract_ssh_key_fingerprint(ssh_key_line: str, algo: str = "md5") -> Optional[str]:
    try:
        parts = ssh_key_line.strip().split()
        if len(parts) < 2:
            return None
        key_type = parts[0]
        key_b64 = parts[1]
        try:
            key_bytes = base64.b64decode(key_b64)
        except Exception:
            return None
        if algo.lower() == "md5":
            digest = hashlib.md5(key_bytes).hexdigest()
            return "MD5:" + ":".join(digest[i:i+2] for i in range(0, len(digest), 2))
        elif algo.lower() == "sha256":
            digest = hashlib.sha256(key_bytes).digest()
            return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
        return None
    except Exception:
        return None


def extract_all_ssh_fingerprints(ssh_keys: List[str]) -> Dict[str, str]:
    fingerprints = {}
    for idx, key_line in enumerate(ssh_keys):
        fp_md5 = extract_ssh_key_fingerprint(key_line, "md5")
        fp_sha256 = extract_ssh_key_fingerprint(key_line, "sha256")
        if fp_md5:
            fingerprints[f"key_{idx}_md5"] = fp_md5
        if fp_sha256:
            fingerprints[f"key_{idx}_sha256"] = fp_sha256
    return fingerprints


def format_date(date_str: Optional[str]) -> str:
    if not date_str:
        return "N/A"
    try:
        if "T" in date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        elif " " in date_str:
            dt = datetime.strptime(date_str, "%Y/%m/%d %H:%M:%S")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y/%m/%d")
    except Exception:
        return date_str


def normalize_email(email: str) -> str:
    if not email:
        return ""
    email = email.strip().lower()
    if "+" in email.split("@")[0]:
        local, domain = email.split("@", 1)
        local = local.split("+")[0]
        email = f"{local}@{domain}"
    return email


def group_emails_by_domain(emails: Set[str]) -> Dict[str, Set[str]]:
    by_domain: Dict[str, Set[str]] = defaultdict(set)
    for email in emails:
        if "@" in email:
            domain = email.split("@")[-1].lower()
            by_domain[domain].add(email)
    return dict(by_domain)


def build_identity_cluster(identities: Dict[str, Set[str]], new_key: str, new_values: Set[str]) -> Dict[str, Set[str]]:
    cluster_key = None
    for existing_key, existing_vals in identities.items():
        if existing_key == new_key or new_key in existing_vals or (new_values & existing_vals):
            cluster_key = existing_key
            break
    if cluster_key is None:
        identities[new_key] = {new_key} | new_values
    else:
        identities[cluster_key].update({new_key} | new_values)
    return identities


def merge_identity_clusters(identities: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    changed = True
    while changed:
        changed = False
        keys = list(identities.keys())
        for i, k1 in enumerate(keys):
            if k1 not in identities:
                continue
            for k2 in keys[i+1:]:
                if k2 not in identities:
                    continue
                if identities[k1] & identities[k2]:
                    identities[k1].update(identities[k2])
                    del identities[k2]
                    changed = True
    return identities


def compute_time_diff_minutes(dt1: datetime, dt2: datetime) -> int:
    return abs(int((dt1 - dt2).total_seconds() / 60))


def is_ordered_diff_sequence(commits_a: List[Dict[str, any]], commits_b: List[Dict[str, any]], time_window_min: int = 60) -> List[Dict[str, any]]:
    ordered_matches = []
    if not commits_a or not commits_b:
        return ordered_matches
    commits_a_sorted = sorted(commits_a, key=lambda x: x.get("timestamp", datetime.min))
    commits_b_sorted = sorted(commits_b, key=lambda x: x.get("timestamp", datetime.min))
    i, j = 0, 0
    while i < len(commits_a_sorted) and j < len(commits_b_sorted):
        c_a = commits_a_sorted[i]
        c_b = commits_b_sorted[j]
        ts_a = c_a.get("timestamp")
        ts_b = c_b.get("timestamp")
        if not ts_a or not ts_b:
            i += 1
            j += 1
            continue
        diff = compute_time_diff_minutes(ts_a, ts_b)
        if diff <= time_window_min:
            ordered_matches.append({
                "commit_a": c_a,
                "commit_b": c_b,
                "time_diff_min": diff,
                "same_repo": c_a.get("repo") == c_b.get("repo")
            })
        if ts_a < ts_b:
            i += 1
        else:
            j += 1
    return ordered_matches


def compute_collaboration_score(ordered_matches: List[Dict[str, any]], total_commits_a: int, total_commits_b: int) -> float:
    if not ordered_matches or total_commits_a == 0 or total_commits_b == 0:
        return 0.0
    same_repo_count = sum(1 for m in ordered_matches if m["same_repo"])
    time_diff_sum = sum(m["time_diff_min"] for m in ordered_matches)
    avg_time_diff = time_diff_sum / len(ordered_matches) if ordered_matches else 999
    time_factor = max(0.0, 1.0 - (avg_time_diff / 60.0))
    match_ratio = len(ordered_matches) / min(total_commits_a, total_commits_b)
    same_repo_factor = same_repo_count / len(ordered_matches) if ordered_matches else 0
    score = (match_ratio * 0.4) + (same_repo_factor * 0.35) + (time_factor * 0.25)
    return round(score * 100, 2)


def build_collaboration_timeline(ordered_matches: List[Dict[str, any]], bucket_size_days: int = 30) -> List[Dict[str, any]]:
    if not ordered_matches:
        return []
    all_timestamps = []
    for m in ordered_matches:
        ts_a = m["commit_a"].get("timestamp")
        ts_b = m["commit_b"].get("timestamp")
        if ts_a:
            all_timestamps.append(ts_a)
        if ts_b:
            all_timestamps.append(ts_b)
    if not all_timestamps:
        return []
    min_ts = min(all_timestamps)
    max_ts = max(all_timestamps)
    start_bucket = datetime(min_ts.year, min_ts.month, 1)
    end_bucket = datetime(max_ts.year, max_ts.month, 1)
    buckets: Dict[datetime, int] = defaultdict(int)
    for ts in all_timestamps:
        bucket_key = datetime(ts.year, ts.month, 1)
        buckets[bucket_key] += 1
    timeline = []
    current = start_bucket
    while current <= end_bucket:
        count = buckets.get(current, 0)
        if count > 0:
            timeline.append({
                "period": current.strftime("%Y-%m"),
                "collaboration_events": count
            })
        current += timedelta(days=30)
        current = datetime(current.year, current.month, 1)
    return timeline


def detect_account_renames(api_data_history: List[Dict[str, any]], current_username: str) -> Dict[str, any]:
    result = {
        "was_renamed": False,
        "previous_usernames": [],
        "last_known_active": current_username,
        "is_deleted": False
    }
    if not api_data_history:
        return result
    seen = set()
    for entry in api_data_history:
        login = entry.get("login", "")
        if login and login not in seen and login.lower() != current_username.lower():
            seen.add(login)
            result["previous_usernames"].append({
                "username": login,
                "date": entry.get("recorded_at", "N/A")
            })
            result["last_known_active"] = login
    if len(result["previous_usernames"]) > 0:
        result["was_renamed"] = True
    return result


def safe_get_key(d: Dict[str, any], key: str, default: any = None) -> any:
    try:
        return d.get(key, default)
    except Exception:
        return default


def deduplicate_preserve_order(seq: List[any]) -> List[any]:
    seen = set()
    result = []
    for item in seq:
        key = str(item).lower() if isinstance(item, str) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
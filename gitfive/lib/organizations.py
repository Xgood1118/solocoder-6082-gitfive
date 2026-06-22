from gitfive.lib.instruments import TrioAliveProgress
from gitfive.lib.utils import *
from gitfive.lib.objects import GitfiveRunner

import trio
from bs4 import BeautifulSoup
from alive_progress import alive_bar
from datetime import datetime
from collections import defaultdict

from base64 import b64decode


def show(runner: GitfiveRunner):
    if not runner.target.orgs:
        print("[-] No organizations found.")
        return False

    print(f"[+] {len(runner.target.orgs)} organization{'s' if len(runner.target.orgs) > 1 else ''} found !\n")

    for nb,org in enumerate(runner.target.orgs):
        print(f'Handle : {org["handle"]}')
        if org["name"]:
            print(f'Name : {org["name"]}')
        if org["website"]["link"]:
            print(f'Website : {org["website"]["link"]}{" (Hosted on Github Pages !)" if org["website"]["ghpages_hosted"] else ""}')
        if org["website_on_main_repo"]["link"]:
            print(f'Website on main repo : {org["website_on_main_repo"]["link"]}{" (Hosted on Github Pages !)" if org["website_on_main_repo"]["ghpages_hosted"] else ""}')
        if org["email"]:
            print(f'Email : {org["email"]}')
        print(f'GH Pages : {"Found" if org["github_pages"]["activated"] else "Not found"}')
        if org["github_pages"]["link"]:
            print(f'GH Pages link : {org["github_pages"]["link"]}')
        if org["github_pages"]["cname"]:
            print(f'GH Pages CNAME : {org["github_pages"]["cname"]}')
        if nb != len(runner.target.orgs)-1:
            print()

async def github_pages_check(runner: GitfiveRunner, github_pages: Dict[str, any], req: httpx.Response,
                                body: BeautifulSoup, repo_name: str, org_name: str):
    if req.status_code == 200:
        if repo_name != org_name:
            github_pages["activated"] = True
            github_pages["link"] = repo_name
        if not is_repo_empty(body):
            default_branch_matches = re.findall(r',"defaultBranch":"(.*?)","', req.text)
            default_branch = default_branch_matches[0]
            cname_file = f"https://raw.githubusercontent.com/{org_name}/{repo_name}/{default_branch}/CNAME"
            req = await runner.as_client.get(cname_file)
            if req.status_code == 200:
                if repo_name == org_name:
                    github_pages["activated"] = True
                domain = req.text.strip()
                sanitized_domains = detect_custom_domain(domain)
                if sanitized_domains:
                    github_pages["cname"] = sanitized_domains[-1]
                    if repo_name == org_name:
                        github_pages["link"] = sanitized_domains[-1]
        return github_pages
    return False

async def fetch_org(runner: GitfiveRunner, org_name: str, out: List[Dict[str, any]]):
    async with runner.limiters["orgs_list"]:
        organization = {
            "handle": org_name
        }
        req = await runner.as_client.get(f"https://github.com/{org_name}")
        body = BeautifulSoup(req.text, 'html.parser')
        name = body.find("h1")
        organization["name"] = name.text.strip() if name else ""
        website_link = body.find("a", {"itemprop": "url"})
        website_link = website_link.text.strip() if website_link else ""
        website_domains = detect_custom_domain(website_link)
        organization["website_domains"] = website_domains or []

        website_ghpages_hosted = is_ghpages_hosted(website_domains[-1]) if website_domains else False
        organization["website"] = {"link": website_link, "ghpages_hosted": website_ghpages_hosted}
        
        email = body.find("a", {"itemprop": "email"})
        organization["email"] = email.text.strip() if email else ""

        req_1 = await runner.as_client.get(f"https://github.com/{org_name}/{org_name}")
        body_1 = BeautifulSoup(req_1.text, 'html.parser')
        repo_website_link = body_1.find("a", {"role": "link"})
        repo_website_link = repo_website_link.text if repo_website_link else ""

        repo_website_domains = detect_custom_domain(website_link)
        repo_website_ghpages_hosted = is_ghpages_hosted(repo_website_domains[-1]) if repo_website_domains else False

        organization["website_on_main_repo"] = {"link": repo_website_link, "ghpages_hosted": repo_website_ghpages_hosted}

        req_2 = await runner.as_client.get(f"https://github.com/{org_name}/{org_name}.github.io")
        body_2 = BeautifulSoup(req_2.text, 'html.parser')
        
        gh_pages_default_result = {
            "activated": False,
            "link": "",
            "cname": ""
        }

        gh_pages_result = await github_pages_check(runner, gh_pages_default_result, req_1, body_1, org_name, org_name)
        if not gh_pages_result:
            gh_pages_result = await github_pages_check(runner, gh_pages_default_result, req_2, body_2, f"{org_name}.github.io", org_name)
            if not gh_pages_result:
                gh_pages_result = gh_pages_default_result

        organization["github_pages"] = gh_pages_result

        out.append(organization)

async def scrape(runner: GitfiveRunner):
    out = []
    req = await runner.as_client.get(f"https://github.com/{runner.target.username}")
    body = BeautifulSoup(req.text, 'html.parser')
    orgs = [x.attrs["aria-label"] for x in body.find_all("a", {"class": "avatar-group-item", "data-hovercard-type": "organization"}) if "itemprop" in x.attrs]
    orgs = [x for x in orgs if x]

    with alive_bar(len(orgs), receipt=False, enrich_print=False, title="Fetching organizations...") as bar:
        instrument = TrioAliveProgress(fetch_org, 1, bar)

        trio.lowlevel.add_instrument(instrument)

        async with trio.open_nursery() as nursery:
            for org in orgs:
                nursery.start_soon(fetch_org, runner, org, out)

        trio.lowlevel.remove_instrument(instrument)
    runner.target.orgs = out


async def fetch_org_info(runner: GitfiveRunner, org_name: str) -> Dict[str, any]:
    org_info = {
        "login": org_name,
        "name": "",
        "description": "",
        "blog": "",
        "email": "",
        "location": "",
        "public_repos": 0,
        "public_members": 0,
        "followers": 0,
        "following": 0,
        "created_at": None,
        "updated_at": None,
        "avatar_url": "",
        "type": "Organization"
    }
    api_data = await runner.api.query(f"/orgs/{org_name}")
    if api_data.get("message") == "Not Found":
        runner.rc.print(f"[-] Organization '{org_name}' not found via API, trying web scrape...", style="yellow")
    else:
        org_info.update({
            "name": api_data.get("name", "") or "",
            "description": api_data.get("description", "") or "",
            "blog": api_data.get("blog", "") or "",
            "email": api_data.get("email", "") or "",
            "location": api_data.get("location", "") or "",
            "public_repos": api_data.get("public_repos", 0),
            "public_members": api_data.get("public_members", 0),
            "followers": api_data.get("followers", 0),
            "following": api_data.get("following", 0),
            "created_at": api_data.get("created_at"),
            "updated_at": api_data.get("updated_at"),
            "avatar_url": api_data.get("avatar_url", "")
        })

    req = await runner.as_client.get(f"https://github.com/{org_name}")
    if req.status_code == 404:
        runner.rc.print(f"[-] Organization '{org_name}' not found.", style="red")
        return {}
    body = BeautifulSoup(req.text, 'html.parser')
    
    web_name = body.find("h1", class_="h2")
    if web_name and not org_info["name"]:
        org_info["name"] = web_name.text.strip()

    desc_tag = body.find("p", class_="color-fg-muted")
    if desc_tag and not org_info["description"]:
        org_info["description"] = desc_tag.text.strip()

    return org_info


async def fetch_org_members_page(runner: GitfiveRunner, org_name: str, page: int,
                                  members_out: Dict[str, Dict[str, any]]):
    async with runner.limiters["org_members"]:
        req = await runner.as_client.get(f"https://github.com/orgs/{org_name}/people?page={page}")
        if req.status_code != 200:
            return
        body = BeautifulSoup(req.text, 'html.parser')
        
        member_items = body.find_all("li", class_="member-list-item")
        if not member_items:
            member_items = body.find_all("div", attrs={"data-hovercard-type": "user"})
        
        usernames = set()
        for item in member_items:
            link = item.find("a", href=True)
            if link and link.has_attr("href"):
                href = link["href"].strip("/")
                if "/" not in href and href:
                    usernames.add(href)
        
        hovercard_links = body.find_all("a", attrs={"data-hovercard-type": "user"})
        for link in hovercard_links:
            if link.has_attr("href"):
                href = link["href"].strip("/")
                if "/" not in href and href and href != org_name:
                    usernames.add(href)
        
        for username in usernames:
            if username not in members_out:
                members_out[username] = {
                    "username": username,
                    "joined_org_at": None,
                    "left_org_at": None,
                    "is_active": True,
                    "profile_fetched": False,
                    "emails": set(),
                    "ssh_fingerprints": set(),
                    "commit_signatures": set(),
                    "display_name": "",
                    "id": None,
                    "linked_accounts": []
                }

        try:
            member_rows = body.find_all("tr", class_="member-list-item-row")
            if not member_rows:
                member_rows = body.find_all("li", class_="member-list-item")
            for row in member_rows:
                time_tag = row.find("relative-time") or row.find("time")
                if time_tag and time_tag.has_attr("datetime"):
                    username_link = row.find("a", attrs={"data-hovercard-type": "user"})
                    if username_link:
                        uname = username_link["href"].strip("/").split("/")[-1]
                        if uname in members_out:
                            if time_tag.get("title") and "joined" in time_tag.get("title", "").lower():
                                members_out[uname]["joined_org_at"] = time_tag["datetime"]
        except Exception:
            pass


async def fetch_member_profile(runner: GitfiveRunner, username: str, member_data: Dict[str, any]):
    async with runner.limiters["member_profile"]:
        try:
            api_data = await runner.api.query(f"/users/{username}")
            if api_data.get("message") in ["Not Found", ""]:
                member_data["is_active"] = False
                member_data["profile_fetched"] = False
                if username not in runner.target.renamed_or_deleted_users:
                    runner.target.renamed_or_deleted_users[username] = {
                        "status": "deleted_or_renamed",
                        "last_known_username": username,
                        "checked_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    }
                return

            member_data["id"] = api_data.get("id")
            raw_name = api_data.get("name", "") or ""
            member_data["display_name"] = unicode_patch(raw_name) if raw_name else ""
            member_data["profile_fetched"] = True
            member_data["is_active"] = True

            created = api_data.get("created_at")
            if created and not member_data.get("joined_org_at"):
                member_data["joined_org_at"] = created

        except Exception:
            pass

        try:
            keys_req = await runner.as_client.get(f"https://github.com/{username}.keys")
            if keys_req.status_code == 200 and keys_req.text.strip():
                key_lines = [l for l in keys_req.text.strip().split("\n") if l.strip()]
                fps = extract_all_ssh_fingerprints(key_lines)
                member_data["ssh_fingerprints"].update(set(fps.values()))
        except Exception:
            pass


async def fetch_member_commits(runner: GitfiveRunner, username: str, member_data: Dict[str, any],
                                org_name: str):
    async with runner.limiters["collab_commits"]:
        try:
            data = await runner.api.query(
                f"/search/commits?q=author:{username.lower()} org:{org_name}&per_page=100&sort=author-date&order=asc"
            )
            if data.get("items"):
                for item in data["items"]:
                    commit = item.get("commit", {})
                    author = commit.get("author", {})
                    email = author.get("email", "")
                    if email and email != "noreply@github.com":
                        member_data["emails"].add(normalize_email(email))
                    name = author.get("name", "")
                    if name and name not in [member_data.get("display_name", ""), username]:
                        if "alternative_names" not in member_data:
                            member_data["alternative_names"] = set()
                        member_data["alternative_names"].add(name)

                    if not member_data.get("joined_org_at") and commit.get("author", {}).get("date"):
                        member_data["joined_org_at"] = commit["author"]["date"]
        except Exception:
            pass


def build_member_account_chains(runner: GitfiveRunner):
    all_emails: Dict[str, Set[str]] = defaultdict(set)
    all_ssh_keys: Dict[str, Set[str]] = defaultdict(set)

    for username, member in runner.target.org_members.items():
        for email in member.get("emails", set()):
            all_emails[email].add(username)
        for fp in member.get("ssh_fingerprints", set()):
            all_ssh_keys[fp].add(username)

    for username, member in runner.target.org_members.items():
        linked = set()
        for email in member.get("emails", set()):
            linked.update(all_emails.get(email, set()))
        for fp in member.get("ssh_fingerprints", set()):
            linked.update(all_ssh_keys.get(fp, set()))
        linked.discard(username)
        member["linked_accounts"] = list(linked)

    clusters: Dict[str, Set[str]] = {}
    for username, member in runner.target.org_members.items():
        cluster_ids = {username}
        for linked in member.get("linked_accounts", []):
            cluster_ids.add(linked)
        for cid in cluster_ids:
            build_identity_cluster(clusters, cid, cluster_ids - {cid})
    clusters = merge_identity_clusters(clusters)

    runner.target.org_member_account_chains = {}
    for cluster_key, members in clusters.items():
        if len(members) > 1:
            chain_id = sorted(members)[0]
            runner.target.org_member_account_chains[chain_id] = {
                "members": list(members),
                "size": len(members),
                "evidence": {
                    "shared_emails": [],
                    "shared_ssh_keys": []
                }
            }
            email_to_members: Dict[str, Set[str]] = defaultdict(set)
            ssh_to_members: Dict[str, Set[str]] = defaultdict(set)
            for m in members:
                mdata = runner.target.org_members.get(m, {})
                for e in mdata.get("emails", set()):
                    email_to_members[e].add(m)
                for s in mdata.get("ssh_fingerprints", set()):
                    ssh_to_members[s].add(m)
            for e, ms in email_to_members.items():
                if len(ms) > 1:
                    runner.target.org_member_account_chains[chain_id]["evidence"]["shared_emails"].append({
                        "email": e,
                        "used_by": list(ms)
                    })
            for s, ms in ssh_to_members.items():
                if len(ms) > 1:
                    runner.target.org_member_account_chains[chain_id]["evidence"]["shared_ssh_keys"].append({
                        "fingerprint": s,
                        "used_by": list(ms)
                    })


def detect_membership_changes(runner: GitfiveRunner):
    for username, member in runner.target.org_members.items():
        history = []
        if member.get("joined_org_at"):
            history.append({
                "event": "joined",
                "date": member["joined_org_at"],
                "source": "commit/join_date"
            })
        if not member.get("is_active", True):
            history.append({
                "event": "left_or_renamed",
                "date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": "api_not_found"
            })
        if username in runner.target.renamed_or_deleted_users:
            info = runner.target.renamed_or_deleted_users[username]
            history.append({
                "event": "deleted_or_renamed",
                "date": info.get("checked_at", "unknown"),
                "source": "profile_check"
            })
        if history:
            def _safe_date_key(evt):
                d = evt.get("date")
                if isinstance(d, str) and d:
                    return d
                return "9999-12-31T23:59:59Z"
            runner.target.org_membership_history[username] = {
                "username": username,
                "last_known_active": (
                    username if member.get("is_active")
                    else runner.target.last_known_active_usernames.get(username, username)
                ),
                "events": sorted(history, key=_safe_date_key)
            }


def show_org_members(runner: GitfiveRunner):
    org_name = runner.target.org_name
    org_info = runner.target.org_info

    runner.rc.print(f"\n🏢 ORGANIZATION ANALYSIS: {org_name}", style="plum2 bold")
    
    if org_info:
        if org_info.get("name"):
            print(f"Name : {org_info['name']}")
        if org_info.get("description"):
            print(f"Description : {org_info['description']}")
        if org_info.get("blog"):
            print(f"Website : {org_info['blog']}")
        if org_info.get("email"):
            print(f"Email : {org_info['email']}")
        if org_info.get("location"):
            print(f"Location : {org_info['location']}")
        print(f"Public repos : {org_info.get('public_repos', 0)}")
        print(f"Public members : {len(runner.target.org_members)}")
        if org_info.get("created_at"):
            print(f"Created : {format_date(org_info['created_at'])}")

    members = runner.target.org_members
    if not members:
        print("[-] No public members found.")
        return

    print(f"\n📋 {len(members)} public member{'s' if len(members) != 1 else ''} found :\n")

    for idx, (username, member) in enumerate(sorted(members.items()), 1):
        status_color = "light_green" if member.get("is_active") else "indian_red"
        status_str = "ACTIVE" if member.get("is_active") else "INACTIVE/RENAMED"
        runner.rc.print(f"[{idx}] @{username}", style=f"bold {status_color}", end="")
        runner.rc.print(f"  [{status_str}]", style=f"italic {status_color}")
        
        if member.get("display_name"):
            print(f"    Name : {member['display_name']}")
        
        lk = member.get("last_known_active")
        if lk and lk != username and not member.get("is_active"):
            runner.rc.print(f"    Last known active username: [bold]{lk}[/bold]", style="yellow")
        
        if member.get("joined_org_at"):
            print(f"    Joined org : ~{format_date(member['joined_org_at'])}")
        if not member.get("is_active"):
            hist = runner.target.org_membership_history.get(username, {})
            for evt in hist.get("events", []):
                if evt["event"] in ["left_or_renamed", "deleted_or_renamed"]:
                    print(f"    Left/Renamed : ~{format_date(evt['date'])} ({evt['source']})")
        
        if member.get("emails"):
            print(f"    Emails ({len(member['emails'])}):")
            for e in sorted(list(member["emails"]))[:10]:
                print(f"      - {e}")
            if len(member["emails"]) > 10:
                print(f"      - ... (+{len(member['emails'])-10} more)")
        
        if member.get("ssh_fingerprints"):
            print(f"    SSH fingerprints ({len(member['ssh_fingerprints'])}):")
            for fp in sorted(list(member["ssh_fingerprints"]))[:5]:
                print(f"      - {fp}")
            if len(member["ssh_fingerprints"]) > 5:
                print(f"      - ... (+{len(member['ssh_fingerprints'])-5} more)")
        
        if member.get("linked_accounts"):
            runner.rc.print(f"    🔗 Linked accounts (via email/SSH):", style="cyan")
            for la in member["linked_accounts"]:
                active = "active" if runner.target.org_members.get(la, {}).get("is_active") else "inactive"
                runner.rc.print(f"      - @{la} [{active}]", style=("light_green" if active == "active" else "indian_red"))
        
        print()

    if runner.target.org_member_account_chains:
        runner.rc.print(f"\n🔗 MULTI-ACCOUNT ASSOCIATION CHAINS ({len(runner.target.org_member_account_chains)} found)", style="plum2 bold")
        for chain_id, chain in sorted(runner.target.org_member_account_chains.items(),
                                       key=lambda x: x[1]["size"], reverse=True):
            runner.rc.print(f"\n  Chain [size={chain['size']}]: {', '.join('@'+m for m in chain['members'])}", style="bold")
            if chain["evidence"]["shared_emails"]:
                print(f"    Evidence - shared emails:")
                for ev in chain["evidence"]["shared_emails"]:
                    print(f"      - {ev['email']} used by: {', '.join('@'+u for u in ev['used_by'])}")
            if chain["evidence"]["shared_ssh_keys"]:
                print(f"    Evidence - shared SSH fingerprints:")
                for ev in chain["evidence"]["shared_ssh_keys"]:
                    print(f"      - {ev['fingerprint']} used by: {', '.join('@'+u for u in ev['used_by'])}")

    if runner.target.org_membership_history:
        print(f"\n📜 MEMBERSHIP CHANGE HISTORY")
        for username, hist in sorted(runner.target.org_membership_history.items()):
            events = hist.get("events", [])
            if isinstance(events, list) and len(events) >= 1:
                lk = hist.get("last_known_active", username)
                suffix = f" → @{lk}" if lk != username else ""
                print(f"\n  @{username}{suffix}:")
                for evt in events:
                    if not isinstance(evt, dict):
                        continue
                    ev_name = evt.get("event", "unknown")
                    ev_label = {
                        "joined": "✅ Joined",
                        "left_or_renamed": "↔️ Left/Renamed",
                        "deleted_or_renamed": "⚠️ Deleted/Renamed"
                    }.get(ev_name, ev_name)
                    ev_date = evt.get("date")
                    ev_source = evt.get("source", "unknown")
                    print(f"    - {ev_label} on {format_date(ev_date)} (via {ev_source})")

    if runner.target.renamed_or_deleted_users:
        runner.rc.print(f"\n⚠️  RENAMED/DELETED USERS ({len(runner.target.renamed_or_deleted_users)})", style="yellow bold")
        for old, info in runner.target.renamed_or_deleted_users.items():
            lk = info.get("last_known_username", old)
            print(f"  @{old} → last known: @{lk} (checked {format_date(info.get('checked_at',''))})")


async def analyze_organization(runner: GitfiveRunner, org_name: str):
    runner.target.org_name = org_name

    runner.tmprinter.out("Fetching organization info...")
    runner.target.org_info = await fetch_org_info(runner, org_name)
    runner.tmprinter.clear()

    if not runner.target.org_info:
        return False

    runner.tmprinter.out("Fetching organization members (listing all pages)...")
    members_dict: Dict[str, Dict[str, any]] = {}
    
    first_req = await runner.as_client.get(f"https://github.com/orgs/{org_name}/people")
    first_body = BeautifulSoup(first_req.text, 'html.parser')
    
    pagination = first_body.find_all("a", class_="pagination") or first_body.find_all("a", attrs={"aria-label": True})
    max_page = 1
    for link in first_body.find_all("a", href=True):
        href = link.get("href", "")
        m = re.search(r"[?&]page=(\d+)", href)
        if m and "people" in href:
            max_page = max(max_page, int(m.group(1)))
    
    page_count_tag = first_body.find("div", class_="table-list-header-count") or first_body.find(string=re.compile(r"\d+\s+members?", re.I))
    if page_count_tag:
        mcount = re.search(r"(\d+)", str(page_count_tag))
        if mcount:
            est_pages = max(1, (int(mcount.group(1)) + 29) // 30)
            max_page = max(max_page, est_pages)
    
    if max_page > 1:
        pages = list(range(1, max_page + 1))
    else:
        pages = list(range(1, 10))
    
    with alive_bar(len(pages), receipt=False, enrich_print=False, title="Listing org members pages...") as bar:
        instrument = TrioAliveProgress(fetch_org_members_page, 1, bar)
        trio.lowlevel.add_instrument(instrument)
        async with trio.open_nursery() as nursery:
            for page in pages:
                nursery.start_soon(fetch_org_members_page, runner, org_name, page, members_dict)
        trio.lowlevel.remove_instrument(instrument)
    
    if not members_dict and first_req.status_code == 200:
        await fetch_org_members_page(runner, org_name, 1, members_dict)
    
    runner.tmprinter.clear()
    runner.target.org_members = members_dict

    if not members_dict:
        runner.rc.print("[!] Could not find public members listing (org may have hidden members list)", style="yellow")
        return True

    member_names = list(members_dict.keys())
    with alive_bar(len(member_names), receipt=False, enrich_print=False, title="Fetching member profiles...") as bar:
        instrument = TrioAliveProgress(fetch_member_profile, 1, bar)
        trio.lowlevel.add_instrument(instrument)
        async with trio.open_nursery() as nursery:
            for uname in member_names:
                nursery.start_soon(fetch_member_profile, runner, uname, members_dict[uname])
        trio.lowlevel.remove_instrument(instrument)

    member_names2 = list(members_dict.keys())
    with alive_bar(len(member_names2), receipt=False, enrich_print=False, title="Fetching member commit emails...") as bar:
        instrument = TrioAliveProgress(fetch_member_commits, 1, bar)
        trio.lowlevel.add_instrument(instrument)
        async with trio.open_nursery() as nursery:
            for uname in member_names2:
                nursery.start_soon(fetch_member_commits, runner, uname, members_dict[uname], org_name)
        trio.lowlevel.remove_instrument(instrument)

    runner.tmprinter.out("Building multi-account association chains...")
    build_member_account_chains(runner)
    runner.tmprinter.clear()

    runner.tmprinter.out("Detecting membership changes and renames...")
    for username, member in members_dict.items():
        if not member.get("is_active"):
            candidates = []
            for other_uname, other in members_dict.items():
                if other.get("is_active"):
                    shared_e = member.get("emails", set()) & other.get("emails", set())
                    shared_s = member.get("ssh_fingerprints", set()) & other.get("ssh_fingerprints", set())
                    if shared_e or shared_s:
                        candidates.append((other_uname, len(shared_e) + len(shared_s)))
            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                runner.target.last_known_active_usernames[username] = candidates[0][0]
            else:
                runner.target.last_known_active_usernames[username] = username
    detect_membership_changes(runner)
    runner.tmprinter.clear()

    return True

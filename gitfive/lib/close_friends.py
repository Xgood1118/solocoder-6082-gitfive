from gitfive.lib.objects import GitfiveRunner, TMPrinter
from gitfive.lib import pea, social, metamon, commits, github
from gitfive.lib.utils import *

from typing import *
from datetime import datetime
from collections import defaultdict


def update_close_friends(username: str, users: Dict[str, Dict[str, any]], reason):
    if username in users:
        users[username]["points"] += 1
        users[username]["reasons"].append(reason)
    else:
        users[username] = {"points": 1, "reasons": [reason]}
    return users


def is_pea(username: str, pea_cache: Dict[str, bool]):
    return pea_cache[username]


async def collect_target_commits(runner: GitfiveRunner) -> List[Dict[str, any]]:
    target_commits = []
    username = runner.target.username

    try:
        data = await runner.api.query(
            f"/search/commits?q=author:{username.lower()}&per_page=100&sort=author-date&order=desc"
        )
        if data.get("items"):
            for item in data["items"]:
                commit = item.get("commit", {})
                author = commit.get("author", {})
                date_str = author.get("date")
                ts = None
                if date_str:
                    try:
                        ts = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        pass
                target_commits.append({
                    "sha": item.get("sha", ""),
                    "timestamp": ts,
                    "repo": item.get("repository", {}).get("full_name", ""),
                    "email": normalize_email(author.get("email", "")),
                    "name": author.get("name", ""),
                    "message": (commit.get("message", "") or "")[:100]
                })
    except Exception:
        pass

    for repo_info in runner.target.repos:
        if not repo_info.get("is_source"):
            continue
        try:
            data = await runner.api.query(
                f"/repos/{username}/{repo_info['name']}/commits?per_page=50&author={username}"
            )
            if isinstance(data, list):
                for item in data:
                    commit = item.get("commit", {})
                    author = commit.get("author", {})
                    date_str = author.get("date")
                    ts = None
                    if date_str:
                        try:
                            ts = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
                        except Exception:
                            pass
                    target_commits.append({
                        "sha": item.get("sha", ""),
                        "timestamp": ts,
                        "repo": f"{username}/{repo_info['name']}",
                        "email": normalize_email(author.get("email", "")),
                        "name": author.get("name", ""),
                        "message": (commit.get("message", "") or "")[:100]
                    })
        except Exception:
            continue

    return target_commits


async def collect_collaborator_commits(runner: GitfiveRunner, repo: str, limit: int = 100) -> Dict[str, List[Dict[str, any]]]:
    collaborator_commits: Dict[str, List[Dict[str, any]]] = defaultdict(list)
    try:
        data = await runner.api.query(f"/repos/{repo}/commits?per_page={limit}")
        if isinstance(data, list):
            for item in data:
                commit = item.get("commit", {})
                author = commit.get("author", {})
                committer = commit.get("committer", {})
                gh_author = item.get("author", {})
                gh_committer = item.get("committer", {})
                date_str = author.get("date") or committer.get("date")
                ts = None
                if date_str:
                    try:
                        ts = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        pass

                def record(person_gh, person_commit):
                    gh_login = (person_gh or {}).get("login", "")
                    email = normalize_email((person_commit or {}).get("email", ""))
                    name = (person_commit or {}).get("name", "")
                    if not gh_login and not email:
                        return
                    key = gh_login or email or name
                    if not key:
                        return
                    collaborator_commits[key].append({
                        "sha": item.get("sha", ""),
                        "timestamp": ts,
                        "repo": repo,
                        "email": email,
                        "name": name,
                        "gh_username": gh_login,
                        "is_github_author": bool(gh_login)
                    })

                record(gh_author, author)
                record(gh_committer, committer)
    except Exception:
        pass
    return dict(collaborator_commits)


async def analyze_commit_graph(runner: GitfiveRunner, time_window_min: int = 60) -> Dict[str, Dict[str, any]]:
    results: Dict[str, Dict[str, any]] = {}

    runner.tmprinter.out("Collecting target commit timeline...")
    target_commits = await collect_target_commits(runner)
    runner.tmprinter.clear()

    if not target_commits:
        runner.rc.print("[!] No target commits found for graph analysis", style="yellow")
        return results

    target_commits_sorted = sorted(
        [c for c in target_commits if c.get("timestamp")],
        key=lambda x: x["timestamp"]
    )

    repos_to_scan = set()
    for c in target_commits:
        if c.get("repo") and c["repo"].count("/") == 1:
            repos_to_scan.add(c["repo"])

    repos_list = list(repos_to_scan)[:50]
    if not repos_list:
        repos_list = [f"{runner.target.username}/{r['name']}" for r in runner.target.repos if r.get("is_source")][:20]

    all_collab: Dict[str, List[Dict[str, any]]] = defaultdict(list)

    with alive_bar(len(repos_list), receipt=False, enrich_print=False, title="Scanning repos for collaborators...") as bar:
        async def scan_repo(repo):
            nonlocal all_collab
            collab = await collect_collaborator_commits(runner, repo, limit=150)
            for key, clist in collab.items():
                all_collab[key].extend(clist)
            bar()

        async with trio.open_nursery() as nursery:
            for repo in repos_list:
                nursery.start_soon(scan_repo, repo)

    target_lower = runner.target.username.lower()
    potential = {}
    for key, clist in all_collab.items():
        first = clist[0]
        gh_user = first.get("gh_username", "")
        email = first.get("email", "")
        name = first.get("name", "")
        if gh_user.lower() == target_lower:
            continue
        identity_key = gh_user or email or key
        if identity_key not in potential:
            potential[identity_key] = {
                "gh_username": gh_user,
                "email": email,
                "name": name,
                "commits": []
            }
        potential[identity_key]["commits"].extend(clist)

    for identity, info in potential.items():
        collab_commits = info["commits"]
        if len(collab_commits) < 3:
            continue

        ordered_matches = is_ordered_diff_sequence(target_commits_sorted, collab_commits, time_window_min)

        same_repo_commits = sum(1 for c in collab_commits if c.get("repo") and any(
            tc.get("repo") == c["repo"] for tc in target_commits_sorted
        ))

        score = compute_collaboration_score(
            ordered_matches,
            len(target_commits_sorted),
            len(collab_commits)
        )

        close_window_pairs = sum(
            1 for m in ordered_matches
            if isinstance(m, dict) and isinstance(m.get("time_diff_min"), (int, float)) and m["time_diff_min"] <= 15
        )

        if score < 5 and len(ordered_matches) < 5:
            continue

        timeline = build_collaboration_timeline(ordered_matches)

        results[identity] = {
            "identity": identity,
            "gh_username": info["gh_username"],
            "email": info["email"],
            "display_name": info["name"],
            "total_collab_commits": len(collab_commits),
            "ordered_matches_count": len(ordered_matches),
            "close_window_pairs": close_window_pairs,
            "same_repo_commits": same_repo_commits,
            "collaboration_score": score,
            "timeline": timeline,
            "evidence": {
                "repos_in_common": sorted(list(set(c["repo"] for c in collab_commits if c.get("repo"))))[:20],
                "emails_used": sorted(list(set(c["email"] for c in collab_commits if c.get("email")))),
                "sample_ordered_matches": ordered_matches[:5]
            }
        }

    sorted_results = dict(sorted(
        results.items(),
        key=lambda x: (
            x[1]["collaboration_score"],
            x[1]["ordered_matches_count"],
            x[1]["close_window_pairs"]
        ),
        reverse=True
    ))

    return sorted_results


async def link_collaborator_accounts(runner: GitfiveRunner, collab_results: Dict[str, Dict[str, any]]) -> Dict[str, Dict[str, any]]:
    runner.tmprinter.out("Linking collaborators to known accounts/emails...")

    all_candidate_emails = set()
    for collab in collab_results.values():
        for e in collab.get("evidence", {}).get("emails_used", []):
            if e:
                all_candidate_emails.add(e)
        if collab.get("email"):
            all_candidate_emails.add(collab["email"])

    known_map: Dict[str, Dict[str, any]] = {}

    for email, reg_info in runner.target.registered_emails.items():
        known_map[normalize_email(email)] = {
            "source": "registered_emails",
            "gh_username": reg_info.get("username", ""),
            "is_target": reg_info.get("is_target", False)
        }

    for email, contrib_info in runner.target.all_contribs.items():
        ne = normalize_email(email)
        if ne not in known_map and contrib_info.get("handle"):
            known_map[ne] = {
                "source": "all_contribs",
                "gh_username": "",
                "handle": contrib_info.get("handle", "")
            }

    for collab_id, collab in collab_results.items():
        linked = {
            "matched_github_account": collab.get("gh_username", ""),
            "matched_emails": [],
            "matched_via_target_internal": False,
            "matched_via_registry": False,
            "is_known_target_account": False
        }

        if collab.get("gh_username"):
            gh_lower = collab["gh_username"].lower()
            for uname in runner.target.usernames:
                if uname.lower() == gh_lower:
                    linked["is_known_target_account"] = True
                    linked["matched_via_target_internal"] = True
                    break

        for e in collab.get("evidence", {}).get("emails_used", []):
            ne = normalize_email(e)
            if ne in known_map:
                info = known_map[ne]
                linked["matched_emails"].append({
                    "email": e,
                    "source": info.get("source", ""),
                    "gh_username": info.get("gh_username", "")
                })
                if info.get("is_target"):
                    linked["is_known_target_account"] = True
                if info.get("source") == "registered_emails":
                    linked["matched_via_registry"] = True
                if info.get("source") == "all_contribs":
                    linked["matched_via_target_internal"] = True
        if collab.get("email"):
            ne = normalize_email(collab["email"])
            if ne in known_map and ne not in [x["email"] for x in linked["matched_emails"]]:
                info = known_map[ne]
                linked["matched_emails"].append({
                    "email": collab["email"],
                    "source": info.get("source", ""),
                    "gh_username": info.get("gh_username", "")
                })

        runner.target.collaborator_account_links[collab_id] = linked

    runner.tmprinter.clear()
    return runner.target.collaborator_account_links


def show(runner: GitfiveRunner):
    users = runner.target.potential_friends
    if users:
        runner.rc.print(f"[+] {len(users)} potential close friend{'s' if len(users) > 1 else ''} found !", style="light_green")

        points = sorted(list(set([x["points"] for x in list(users.values())])), reverse=True)
        for point in points :
            to_show = []
            for username in users:
                if users[username]["points"] == point:
                    to_show.append(username)
            print(f"\nClose friend{'s' if len(to_show) > 1 else ''} with {point} point{'s' if point > 1 else ''} :")
            for username in to_show[:14]:
                print(f"- {username} ({', '.join(users[username]['reasons'])})")
            if len(to_show) > 14:
                print("- [...]")
    else:
        print("[-] No potential close friends were found.")

    print("\n* PEA = Pretty Empty Account")


def show_close_collaborators(runner: GitfiveRunner):
    collabs = runner.target.close_collaborators
    if not collabs:
        runner.rc.print("[-] No close collaborators identified via commit graph analysis.", style="yellow")
        return

    runner.rc.print(f"\n🤝 CLOSE COLLABORATORS RANKING ({len(collabs)} found)", style="deep_pink2 bold")

    for rank, (identity, data) in enumerate(collabs.items(), 1):
        score = data.get("collaboration_score", 0)
        matches = data.get("ordered_matches_count", 0)
        close_pairs = data.get("close_window_pairs", 0)
        total = data.get("total_collab_commits", 0)
        same_repo = data.get("same_repo_commits", 0)

        label_parts = []
        if data.get("gh_username"):
            label_parts.append(f"@{data['gh_username']}")
        if data.get("email"):
            label_parts.append(data["email"])
        if not label_parts:
            label_parts.append(identity)
        label = " / ".join(label_parts)

        score_color = "light_green bold" if score >= 40 else ("cyan" if score >= 20 else "white")
        runner.rc.print(f"\n[#{rank}] {label}", style="bold", end="")
        runner.rc.print(f"  Score: {score}", style=score_color)

        if data.get("display_name") and data["display_name"] != data.get("gh_username", ""):
            print(f"    Name : {data['display_name']}")

        print(f"    Metrics: {matches} ordered matches ({close_pairs} ≤15min), {total} commits, {same_repo} same-repo")

        link_info = runner.target.collaborator_account_links.get(identity, {})
        if link_info:
            if link_info.get("matched_github_account"):
                runner.rc.print(f"    🔗 Linked GH: @{link_info['matched_github_account']}", style="cyan")
            if link_info.get("is_known_target_account"):
                runner.rc.print(f"    ⚠️  WARNING: This appears to be a target-owned alternate account!", style="yellow bold")
            if link_info.get("matched_emails"):
                emails_str = ", ".join(f"{m['email']}(via {m['source']})" for m in link_info["matched_emails"][:3])
                runner.rc.print(f"    📧 Linked emails: {emails_str}", style="cyan")

        ev = data.get("evidence", {})
        repos = ev.get("repos_in_common", [])
        if repos:
            print(f"    Repos ({len(repos)}): {', '.join(repos[:8])}{' ...' if len(repos) > 8 else ''}")

        timeline = data.get("timeline", [])
        if timeline and isinstance(timeline, list):
            valid_tl = [t for t in timeline if isinstance(t, dict) and isinstance(t.get("collaboration_events"), int)]
            if valid_tl:
                total_events = sum(t["collaboration_events"] for t in valid_tl)
                peak = max(valid_tl, key=lambda x: x["collaboration_events"])
                peak_period = peak.get("period", "unknown")
                peak_count = peak.get("collaboration_events", 0)
                print(f"    Timeline: {len(valid_tl)} months, peak {peak_period} ({peak_count} events)")
                bar_max = max(t["collaboration_events"] for t in valid_tl)
                for t in valid_tl:
                    bar_len = int(t["collaboration_events"] / bar_max * 20) if bar_max > 0 else 0
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    print(f"      {t.get('period', '?')} |{bar}| {t['collaboration_events']}")

    runner.rc.print("\n📊 Collaboration Score = (match_ratio × 0.4) + (same_repo_factor × 0.35) + (time_factor × 0.25)", style="italic dim")
    print("   Ordered match = two commits from target & collaborator within time window in commit sequence")


async def guess(runner: GitfiveRunner):
    users = {}
    target = {}

    tmprinter = TMPrinter()
    tmprinter.out("Analyzing if target is PEA...")

    pea_cache = await pea.analyze(runner, [runner.target.username])
    tmprinter.clear()

    target["is_pea"] = is_pea(runner.target.username, pea_cache)
    print(f'Account is PEA : {target["is_pea"]}')

    target["following"] = await social.get_follows(runner, "following")

    if not target["is_pea"] and not target["following"]:
        return {}

    target["followers"] = await social.get_follows(runner, "followers")

    if target["is_pea"]:
        usernames = target["following"].union(target["followers"])
    else:
        usernames = target["following"]

    new_pea_cache = await pea.analyze(runner, usernames)
    pea_cache = pea_cache | new_pea_cache

    if target["is_pea"]:
        for username in target["followers"]:
            users = update_close_friends(username, users, "Follower is following PEA")
            if is_pea(username, pea_cache):
                users = update_close_friends(username, users, "Follower is PEA")

    for username in target["following"]:
        if is_pea(username, pea_cache):
            users = update_close_friends(username, users, "Following is PEA")
        if username in target["followers"]:
            users = update_close_friends(username, users, "Follower + Following")

    users = {k: v for k, v in sorted(users.items(), key=lambda item: item[1]["points"], reverse=True)}
    return users


async def guess_enhanced(runner: GitfiveRunner, use_social: bool = True, use_commit_graph: bool = True) -> Dict[str, Dict[str, any]]:
    social_results = {}
    graph_results = {}

    if use_social:
        runner.rc.print("\n[Close Friends] Running social-based analysis...", style="italic")
        social_results = await guess(runner)

    if use_commit_graph:
        runner.rc.print("\n[Close Friends] Running commit-graph ordered-diff analysis...", style="italic")
        graph_results = await analyze_commit_graph(runner, time_window_min=90)
        runner.target.close_collaborators = graph_results

        if graph_results and runner.target.all_contribs or runner.target.registered_emails:
            await link_collaborator_accounts(runner, graph_results)

    combined = {}

    for uname, sdata in social_results.items():
        combined[uname] = {
            "source": "social",
            "points": sdata["points"],
            "reasons": sdata["reasons"],
            "gh_username": uname,
            "email": "",
            "collaboration_score": 0,
            "timeline": []
        }

    for identity, gdata in graph_results.items():
        score = gdata.get("collaboration_score", 0)
        rank_points = min(int(score / 5), 20)
        key = gdata.get("gh_username") or identity
        if key in combined:
            combined[key]["source"] += "+graph"
            combined[key]["points"] += rank_points
            combined[key]["collaboration_score"] = score
            combined[key]["email"] = gdata.get("email", "")
            combined[key]["timeline"] = gdata.get("timeline", [])
            combined[key]["reasons"].append(f"High commit-graph collaboration ({score}/100)")
            if gdata.get("close_window_pairs", 0) >= 3:
                combined[key]["reasons"].append("Frequent ≤15min commit pairs")
        else:
            combined[key] = {
                "source": "graph",
                "points": rank_points,
                "reasons": [
                    f"Commit-graph collaboration score {score}/100",
                    f"{gdata.get('ordered_matches_count', 0)} ordered matches"
                ],
                "gh_username": gdata.get("gh_username", ""),
                "email": gdata.get("email", ""),
                "collaboration_score": score,
                "timeline": gdata.get("timeline", [])
            }

    combined_sorted = {
        k: v for k, v in sorted(
            combined.items(),
            key=lambda x: (x[1]["points"], x[1].get("collaboration_score", 0)),
            reverse=True
        )
    }

    return combined_sorted

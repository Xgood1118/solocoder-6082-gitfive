from gitfive.lib.objects import GitfiveRunner
from gitfive.lib import organizations, close_friends, repos, xray, emails_gen, metamon, commits, github
from gitfive.lib.utils import *
import gitfive.config as config

from pathlib import Path


async def hunt(org_name: str, json_file="", runner: GitfiveRunner=None, with_close_friends: bool=True):
    if not runner:
        runner = GitfiveRunner()
        await runner.login()

    ok = await organizations.analyze_organization(runner, org_name)
    if not ok:
        exit(f'\n[-] Failed to analyze organization "{org_name}".')

    organizations.show_org_members(runner)

    if with_close_friends and runner.target.org_members:
        runner.rc.print(f"\n🎎 CLOSE FRIENDS (per member) / ORG-WIDE COLLABORATION", style="deep_pink2")

        member_list = list(runner.target.org_members.keys())
        sample_size = min(len(member_list), 15)
        runner.rc.print(f"\n[*] Running enhanced close-friends analysis on top {sample_size}/{len(member_list)} active members...", style="italic")

        org_collab_summary: Dict[str, Dict[str, any]] = {}

        for idx, member_name in enumerate(member_list[:sample_size]):
            member = runner.target.org_members.get(member_name, {})
            if not member.get("is_active"):
                continue
            runner.rc.print(f"\n  [{idx+1}/{sample_size}] Analyzing @{member_name} ...", style="dim italic", end="")

            try:
                runner.tmprinter.out(f"  Fetching profile for @{member_name}...")
                member_api = await runner.api.query(f"/users/{member_name}")
                runner.tmprinter.clear()
                if member_api.get("message") == "Not Found":
                    runner.rc.print(" → not found (renamed/deleted)", style="yellow italic")
                    continue

                saved_target = {
                    "username": runner.target.username,
                    "name": runner.target.name,
                    "id": runner.target.id,
                    "type": runner.target.type if hasattr(runner.target, "type") else "User",
                    "nb_public_repos": runner.target.nb_public_repos,
                    "created_at": runner.target.created_at,
                    "is_site_admin": runner.target.is_site_admin,
                    "company": runner.target.company,
                    "blog": runner.target.blog,
                    "location": runner.target.location,
                    "bio": runner.target.bio,
                    "twitter": runner.target.twitter,
                    "avatar_url": runner.target.avatar_url,
                    "is_default_avatar": runner.target.is_default_avatar,
                    "nb_followers": runner.target.nb_followers,
                    "nb_following": runner.target.nb_following,
                    "nb_ext_contribs": runner.target.nb_ext_contribs,
                    "updated_at": runner.target.updated_at,
                    "is_hireable": runner.target.is_hireable if hasattr(runner.target, "is_hireable") else False
                }

                runner.target.username = member_name
                runner.target._scrape(member_api)

                await repos.get_list(runner)

                graph_res = await close_friends.analyze_commit_graph(runner, time_window_min=90)
                if graph_res:
                    org_collab_summary[member_name] = {
                        "display_name": member.get("display_name", ""),
                        "top_collaborators": list(graph_res.items())[:5],
                        "total_collabs": len(graph_res)
                    }
                    runner.rc.print(f" → {len(graph_res)} collaborator(s)", style="light_green italic")
                else:
                    runner.rc.print(" → none found", style="dim italic")

                runner.target.username = saved_target["username"]
                runner.target.name = saved_target["name"]
                runner.target.id = saved_target["id"]
                runner.target.nb_public_repos = saved_target["nb_public_repos"]
                runner.target.created_at = saved_target["created_at"]
                runner.target.is_site_admin = saved_target["is_site_admin"]
                runner.target.company = saved_target["company"]
                runner.target.blog = saved_target["blog"]
                runner.target.location = saved_target["location"]
                runner.target.bio = saved_target["bio"]
                runner.target.twitter = saved_target["twitter"]
                runner.target.avatar_url = saved_target["avatar_url"]
                runner.target.is_default_avatar = saved_target["is_default_avatar"]
                runner.target.nb_followers = saved_target["nb_followers"]
                runner.target.nb_following = saved_target["nb_following"]
                runner.target.nb_ext_contribs = saved_target["nb_ext_contribs"]
                runner.target.updated_at = saved_target["updated_at"]
                if hasattr(runner.target, "is_hireable"):
                    runner.target.is_hireable = saved_target["is_hireable"]

            except Exception as e:
                runner.rc.print(f" → error: {str(e)[:60]}", style="red italic")
                runner.tmprinter.clear()
                continue

        if org_collab_summary:
            runner.rc.print(f"\n📊 ORG-WIDE COLLABORATION SUMMARY", style="deep_pink2 bold")
            cross_org: Dict[str, int] = {}
            for member, summary in org_collab_summary.items():
                print(f"\n  @{member} ({summary['display_name']}): {summary['total_collabs']} collaborators")
                for collab_id, cdata in summary["top_collaborators"]:
                    score = cdata.get("collaboration_score", 0)
                    label = cdata.get("gh_username") or cdata.get("email") or collab_id
                    in_org = label.lstrip("@") in runner.target.org_members
                    tag = " [ORG MEMBER]" if in_org else ""
                    print(f"    - {label} (score {score}){tag}")
                    if in_org:
                        pair_key = tuple(sorted([member.lower(), label.lower().lstrip("@")]))
                        cross_org[pair_key] = cross_org.get(pair_key, 0) + int(score)

            if cross_org:
                runner.rc.print(f"\n🔗 STRONGEST INTRA-ORG COLLABORATIONS (top pairs)", style="plum2 bold")
                sorted_pairs = sorted(cross_org.items(), key=lambda x: x[1], reverse=True)[:10]
                for (u1, u2), total_s in sorted_pairs:
                    active_1 = runner.target.org_members.get(u1, {}).get("is_active", True)
                    active_2 = runner.target.org_members.get(u2, {}).get("is_active", True)
                    status = "ACTIVE" if (active_1 and active_2) else "(inactive member)"
                    runner.rc.print(f"  @{u1} ↔ @{u2}: combined score {total_s} {status}", style="cyan")

    if json_file:
        from pathlib import Path as P
        parent = P(json_file).parent
        if parent and not parent.is_dir():
            exit(f"[-] The directory {parent} can't be found.")
        import json
        with open(json_file, "w", encoding="utf-8") as f:
            data = {
                "org_info": runner.target.org_info,
                "org_members": {},
                "org_member_account_chains": runner.target.org_member_account_chains,
                "org_membership_history": runner.target.org_membership_history,
                "renamed_or_deleted_users": runner.target.renamed_or_deleted_users,
                "last_known_active_usernames": runner.target.last_known_active_usernames,
                "close_collaborators_per_member": {}
            }
            for uname, m in runner.target.org_members.items():
                md = {}
                for k, v in m.items():
                    if isinstance(v, set):
                        md[k] = list(v)
                    else:
                        md[k] = v
                data["org_members"][uname] = md

            for k, v in getattr(runner.target, "close_collaborators", {}).items():
                import copy
                vc = copy.deepcopy(v)
                if "evidence" in vc and "sample_ordered_matches" in vc["evidence"]:
                    vc["evidence"]["sample_ordered_matches"] = []
                data["close_collaborators_per_member"][k] = vc

            f.write(json.dumps(data, cls=lambda x: str(x) if isinstance(x, datetime) else None, indent=4))
        runner.rc.print(f"\n[+] JSON output wrote to {json_file} !", style="italic")

    runner.tmprinter.out("Deleting temp folder...")
    from gitfive.lib.utils import delete_tmp_dir; delete_tmp_dir()
    runner.tmprinter.clear()

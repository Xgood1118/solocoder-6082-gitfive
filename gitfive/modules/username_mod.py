from gitfive.lib import repos
from gitfive.lib import close_friends
from gitfive.lib.domain_finder import guess_custom_domain
from gitfive.lib import emails_gen
from gitfive.lib import metamon
from gitfive.lib import organizations
from gitfive.lib.utils import *
from gitfive.lib import xray
from gitfive.lib.objects import GitfiveRunner
from gitfive.lib import commits
from gitfive.lib import github
from gitfive.lib.xray import analyze_ext_contribs
import gitfive.config as config


async def hunt(username: str, json_file="", runner: GitfiveRunner=None,
                full_collab: bool=False, no_social: bool=False):
    if not runner:
        runner = GitfiveRunner()
        await runner.login()

    data = await runner.api.query(f"/users/{username}")
    if data.get("message") == "Not Found":
        exit(f'\n[-] User "{username}" not found.')

    runner.target._scrape(data)

    if runner.target.type == "Organization":
        exit("\n[-] GitFive's `user` command targets accounts only. Use `gitfive org <orgname>` for organization analysis.")

    runner.rc.print("\n✍️ PROFILE", style="navajo_white1")

    gist_stats = get_gists_stats(runner)

    runner.tmprinter.out("Getting external contributions...")
    data1 = await runner.api.query(f"/search/commits?q=author:{runner.target.username.lower()} -user:{runner.target.username.lower()}&per_page=100&sort=author-date&order=asc")
    if data1.get("message") == "Validation Failed":
        print(f'\n[-] Failed to grab external contributions, does "{username}" have a private profile?')
    else:
        await analyze_ext_contribs(runner)
    runner.tmprinter.clear()

    print("\n[Identifiers]")
    print(f"Username : {runner.target.username}")
    if runner.target.name:
        print(f'Name : {runner.target.name}')
    else:
        runner.rc.print("Name : [italic]Empty")
    print(f"ID : {runner.target.id}")

    print("\n[Avatar]")
    if runner.target.is_default_avatar:
        print("[-] Default profile pic !")
    else:
        runner.rc.print("[+] Custom profile pic !", style="light_green")
        print(f"=> {runner.target.avatar_url}")

    print("\n[Status]")
    print(f'Site Admin : {"Yes !" if runner.target.is_site_admin else "No"}')
    print(f'Hireable : {"Yes !" if runner.target.is_hireable else "No"}')

    print("\n[Social]")
    if not (runner.target.blog or runner.target.twitter) :
        print("Nothing to show.")
    if runner.target.blog:
        print(f"Site : {runner.target.blog}")
    if runner.target.twitter:
        print(f"Twitter : @{runner.target.twitter}")

    if runner.target.company or runner.target.location or runner.target.bio:
        print("\n[Details]")
    if runner.target.company:
        print(f"Employed at : {runner.target.company}")
    if runner.target.location:
        print(f"Location : {runner.target.location}")
    if runner.target.bio:
        print(f"Biography : {runner.target.bio}")

    print("\n[Stats]")
    print(f"Public repos : {runner.target.nb_public_repos}")
    print(f"Followers : {runner.target.nb_followers}")
    print(f"Following : {runner.target.nb_following}")

    if runner.target.created_at or runner.target.updated_at:
        print("\n[Account]")
    if runner.target.created_at:
        print(f"Account created : {runner.target.created_at.strftime('%Y/%m/%d %H:%M:%S')} (UTC)")
    if runner.target.updated_at:
        print(f"Last profile update : {runner.target.updated_at.strftime('%Y/%m/%d %H:%M:%S')} (UTC)")

    print("\n[External contributions]")
    if runner.target.nb_ext_contribs:
        runner.rc.print(f"[+] External contributions (commits) : {runner.target.nb_ext_contribs}", style="light_green")
        ext_emails = [x for x in runner.target.ext_contribs if not x.endswith("users.noreply.github.com")]
        if ext_emails:
            print(f"Email{'s' if len(ext_emails) > 1 else ''} found :")
            for email in ext_emails:
                print(f"- {email}")
        else:
            print("No email address found.")
        
    else:
        print("Nothing to show.")

    print("\n[SSH public keys]")
    await get_ssh_keys(runner)
    if runner.target.ssh_keys:
        _nb_keys = len(runner.target.ssh_keys)
        runner.rc.print(f"[+] 🔐 Found {_nb_keys} SSH public key{'s' if _nb_keys > 1 else ''} !", style="light_green")
        fps = extract_all_ssh_fingerprints(runner.target.ssh_keys)
        if fps:
            runner.target.ssh_key_fingerprints = fps
            print(f"  Fingerprints extracted: {len(fps)} (MD5 + SHA256)")
        runner.rc.print("Visible in the JSON output, if specified.", style="italic")
    else:
        print("Nothing to show.")

    print("\n[Gists]")
    if not sum(list(gist_stats.values())):
        print("Nothing to show.")
    if gist_stats['gists']:
        print(f"Gists : {gist_stats['gists']}")
    if gist_stats['starred']:
        print(f"Starred : {gist_stats['starred']}")

    if runner.target.company:
        print()
        out = guess_custom_domain(runner)
        for company_domain in out:
            domains = set(detect_custom_domain(company_domain))
            for domain in domains:
                runner.target.domains.add(company_domain)

    if runner.target.blog:
        new_custom_domain = False
        domains = set(detect_custom_domain(runner.target.blog))
        for domain in domains:
            if domain not in runner.target.domains:
                if not new_custom_domain:
                    print()
                    new_custom_domain = True
                runner.rc.print(f"[+] Found possible personal domain : {domain}", style="light_green")
                runner.target.domains.add(domain)

    runner.target._add_name(runner.target.username)
    runner.target._add_name(runner.target.twitter)
    runner.target._add_name(runner.target.name)

    runner.rc.print("\n🏭 REPOSITORIES STATS\n", style="light_salmon1")

    await repos.get_list(runner)
    repos.show(runner)

    runner.rc.print("\n🎎 CLOSE FRIENDS\n", style="deep_pink2")

    use_social = not no_social
    use_graph = full_collab

    if full_collab:
        runner.target.potential_friends = await close_friends.guess_enhanced(
            runner,
            use_social=use_social,
            use_commit_graph=use_graph
        )
        close_friends.show(runner)
        close_friends.show_close_collaborators(runner)
    else:
        if use_social:
            runner.target.potential_friends = await close_friends.guess(runner)
            close_friends.show(runner)
        else:
            runner.rc.print("[!] --no-social requires --full-collab to enable commit-graph analysis.", style="yellow")
            runner.target.potential_friends = await close_friends.guess(runner)
            close_friends.show(runner)

    runner.rc.print("\n🏯 ORGANIZATIONS\n", style="plum2")

    await organizations.scrape(runner)
    organizations.show(runner)

    if runner.target.orgs:
        print()
        for org in runner.target.orgs:
            for dom in org["website_domains"]:
                if dom not in runner.target.domains:
                    runner.rc.print(f"Adding domain -> {dom}", style="italic")
                    runner.target.domains.add(dom)
            if org["email"] and "@" in org["email"]:
                domains = detect_custom_domain(org["email"].split("@")[-1])
                for dom in domains:
                    if not dom in runner.target.domains:
                        runner.rc.print(f"Adding domain -> {dom}", style="italic")
                        runner.target.domains.update(domains)

    runner.rc.print("\n🎭 IDENTITIES UNMASKING\n", style="red3")
    
    await xray.analyze(runner)

    temp_repo_name = ""
    emails_index = {}
    while True:
        emails = emails_gen.generate(runner, default_domains_list=config.emails_default_domains,
        domain_prefixes=config.email_common_domains_prefixes)

        runner.target.generated_emails.update(emails)

        if not emails:
            print("\n[-] No more emails have been generated.")
            break
        runner.rc.print(f"\n[+] {len(emails)} potential email{'s' if len(emails) > 1 else ''} generated !", style="light_green")

        temp_repo_name, emails_index = await metamon.start(runner, emails)
        emails_accounts = {}
        if emails_index:
            emails_accounts = await commits.scrape(runner, temp_repo_name, emails_index)
        
        runner.target.registered_emails |= emails_accounts

        new_usernames = False
        for email, email_data in emails_accounts.items():
            if email_data["is_target"]:
                runner.target.emails.add(email)
                handle = email.split("@")[0].split("+")[0]
                if handle.lower() in {x.lower() for x in runner.target.usernames}:
                    continue
                if not new_usernames:
                    print()
                    new_usernames = True
                runner.target._add_name(handle)
                print(f"[+] New valid username : {handle}")
        if new_usernames:
            print()
        if {x.lower() for x in runner.target.usernames} == {x.lower() for x in runner.analyzed_usernames}:
            break
        new_variations = xray.near_lookup(runner)
        if not new_variations:
            print("[-] No more name variation have been found.")
            break
        xray.near_show(runner)

    if temp_repo_name:
        runner.rc.print("\n[+] Deleted the remote repo", style="italic")
        await github.delete_repo(runner, temp_repo_name)
    
    if json_file:
        import json
        with open(json_file, "w", encoding="utf-8") as f:
            f.write(runner.target.export_json())
        runner.rc.print(f"[+] JSON output wrote to {json_file} !", style="italic")

    runner.tmprinter.out("Deleting temp folder...")
    from gitfive.lib.utils import delete_tmp_dir; delete_tmp_dir()
    runner.tmprinter.clear()


async def hunt_close_friends_only(username: str, json_file="", use_social: bool=True, runner: GitfiveRunner=None):
    if not runner:
        runner = GitfiveRunner()
        await runner.login()

    data = await runner.api.query(f"/users/{username}")
    if data.get("message") == "Not Found":
        exit(f'\n[-] User "{username}" not found.')

    runner.target._scrape(data)

    if runner.target.type == "Organization":
        exit("\n[-] This command targets users only. Use `gitfive org <orgname>` for organization analysis.")

    print(f"\n🎯 Target: @{runner.target.username} ({runner.target.name or 'N/A'})")

    runner.tmprinter.out("Fetching repos list...")
    await repos.get_list(runner)
    runner.tmprinter.clear()

    runner.rc.print("\n🎎 CLOSE FRIENDS / CLOSE COLLABORATORS ANALYSIS", style="deep_pink2 bold")
    print(f"  Social analysis: {'ON' if use_social else 'OFF'}")
    print(f"  Commit-graph ordered-diff analysis: ON (always for this command)")
    print()

    runner.target.potential_friends = await close_friends.guess_enhanced(
        runner,
        use_social=use_social,
        use_commit_graph=True
    )

    close_friends.show(runner)
    close_friends.show_close_collaborators(runner)

    if json_file:
        import json
        with open(json_file, "w", encoding="utf-8") as f:
            export_data = {
                "target": {
                    "username": runner.target.username,
                    "name": runner.target.name,
                    "id": runner.target.id
                },
                "potential_friends": runner.target.potential_friends,
                "close_collaborators": {},
                "collaborator_account_links": runner.target.collaborator_account_links,
                "commit_graph_analysis": {}
            }
            for k, v in runner.target.close_collaborators.items():
                import copy
                vc = copy.deepcopy(v)
                if "evidence" in vc and "sample_ordered_matches" in vc["evidence"]:
                    vc["evidence"]["sample_ordered_matches"] = []
                export_data["close_collaborators"][k] = vc
            f.write(json.dumps(export_data, default=str, indent=4))
        runner.rc.print(f"\n[+] JSON output wrote to {json_file} !", style="italic")

    runner.tmprinter.out("Deleting temp folder...")
    from gitfive.lib.utils import delete_tmp_dir; delete_tmp_dir()
    runner.tmprinter.clear()

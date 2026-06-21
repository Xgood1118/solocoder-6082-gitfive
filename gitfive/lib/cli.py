import argparse
import sys


def parse_args():
    parser = argparse.ArgumentParser('gitfive')
    subparsers = parser.add_subparsers(dest='command')

    login_parser = subparsers.add_parser('login', help='Let GitFive authenticate to GitHub.')
    login_parser.add_argument('--clean', action='store_true', help="Clear credentials and session local files.")

    user_parser = subparsers.add_parser('user', help='Track down a GitHub user by its username.')
    user_parser.add_argument(dest="username",
                            action='store',
                            type=str,
                            help="GitHub's username of the target")
    user_parser.add_argument('--json', type=str, help="File to write the JSON output to")
    user_parser.add_argument('--full-collab', action='store_true',
                            help="Run enhanced close-friends analysis via commit-graph ordered-diff")
    user_parser.add_argument('--no-social', action='store_true',
                            help="Skip social-based close friends (only commit-graph when --full-collab is used)")

    email_parser = subparsers.add_parser('email', help='Track down a GitHub user by its email address.')
    email_parser.add_argument(dest="email_address",
                            action='store',
                            type=str,
                            help="GitHub's email address of the target")
    email_parser.add_argument('--json', type=str, help="File to write the JSON output to")

    emails_parser = subparsers.add_parser('emails', help='Find GitHub usernames of a given list of email addresses.')
    emails_parser.add_argument(dest="emails_file",
                                action='store',
                                type=str,
                                help="File containing a list of email adresses")
    emails_parser.add_argument('--json', type=str, help="File to write the JSON output to")
    emails_parser.add_argument('-t', type=str, help="GitHub's username of the target")

    light_parser = subparsers.add_parser('light', help='Quickly find emails addresses from a GitHub username.')
    light_parser.add_argument(dest="username",
                                action='store',
                                type=str,
                                help="GitHub's username of the target")

    org_parser = subparsers.add_parser('org', help='Analyze a GitHub organization: list public members with multi-account chains, membership history, and intra-org collaboration.')
    org_parser.add_argument(dest="org_name",
                            action='store',
                            type=str,
                            help="GitHub organization handle (e.g. google, facebook)")
    org_parser.add_argument('--json', type=str, help="File to write the JSON output to")
    org_parser.add_argument('--skip-collab', action='store_true',
                            help="Skip per-member close-friends analysis (faster)")

    friends_parser = subparsers.add_parser('close-friends', help='Run enhanced close-collaborator analysis for a user via commit-graph ordered-diff')
    friends_parser.add_argument(dest="username",
                                action='store',
                                type=str,
                                help="GitHub's username of the target")
    friends_parser.add_argument('--json', type=str, help="File to write the JSON output to")
    friends_parser.add_argument('--no-social', action='store_true',
                                help="Skip social-based analysis (graph only)")

    args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])

    import trio
    match args.command:
        case "login":
            from gitfive.modules import login_mod
            trio.run(login_mod.check_and_login, args.clean)
        case "user":
            from gitfive.modules import username_mod
            if not args.username:
                exit("[-] Please give a valid username.\nExample : gitfive user mxrch")
            trio.run(username_mod.hunt, args.username, args.json, args.full_collab, args.no_social)
        case "email":
            from gitfive.modules import email_mod
            if not args.email_address:
                exit("[-] Please give a valid email address.\nExample : gitfive email <email_address>")
            trio.run(email_mod.hunt, args.email_address, args.json)
        case "emails":
            from gitfive.modules import emails_mod
            if not args.emails_file:
                exit("[-] Please give a valid file.\nExample : gitfive emails ~/Desktop/my_emails_list.txt")
            trio.run(emails_mod.hunt, args.emails_file, args.json, args.t)
        case "light":
            from gitfive.modules import light_mod
            if not args.username:
                exit("[-] Please give a valid username.\nExample : gitfive light mxrch")
            trio.run(light_mod.hunt, args.username)
        case "org":
            from gitfive.modules import org_mod
            if not args.org_name:
                exit("[-] Please give a valid organization name.\nExample : gitfive org google")
            trio.run(org_mod.hunt, args.org_name, args.json, not args.skip_collab)
        case "close-friends":
            from gitfive.modules import username_mod
            if not args.username:
                exit("[-] Please give a valid username.\nExample : gitfive close-friends mxrch")
            trio.run(username_mod.hunt_close_friends_only, args.username, args.json, not args.no_social)

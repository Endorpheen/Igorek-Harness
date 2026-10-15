"""Enable and verify private vulnerability reporting after owner publication."""
import argparse
import json
import subprocess


def api(*args):
    result = subprocess.run(['gh', 'api', *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, help='Existing owner/repository')
    args = parser.parse_args()
    if args.repository.count('/') != 1 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/' for c in args.repository):
        parser.error('Use an explicit owner/repository name')
    repository = api('repos/' + args.repository)
    if repository.get('private'):
        raise SystemExit('Repository remains private; public visibility requires the owner decision.')
    api('-X', 'PUT', 'repos/' + args.repository + '/private-vulnerability-reporting')
    result = api('repos/' + args.repository + '/private-vulnerability-reporting')
    if result.get('enabled') is not True:
        raise SystemExit('GitHub did not confirm private vulnerability reporting')
    print('Private vulnerability reporting: enabled and verified.')

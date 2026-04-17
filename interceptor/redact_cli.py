"""Simple CLI to manage and test redaction policies."""
import argparse
from interceptor.redact_policy import load_policy, save_policy


def cmd_list(args):
    rules = load_policy()
    if not rules:
        print('No policy rules configured')
        return
    for pat, repl in rules:
        print(f'- pattern: {pat} -> {repl}')


def cmd_add(args):
    rules = []
    existing = load_policy()
    for pat, repl in existing:
        rules.append({'pattern': pat, 'replace': repl})
    rules.append({'pattern': args.pattern, 'replace': args.replace or '<REDACTED>'})
    save_policy(rules)
    print('Added rule')


def cmd_test(args):
    from interceptor.redact import redact_text
    s = args.text
    print(redact_text(s))


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest='cmd')
    sp_list = sp.add_parser('list')
    sp_list.set_defaults(func=cmd_list)
    sp_add = sp.add_parser('add')
    sp_add.add_argument('pattern')
    sp_add.add_argument('--replace', '-r')
    sp_add.set_defaults(func=cmd_add)
    sp_test = sp.add_parser('test')
    sp_test.add_argument('text')
    sp_test.set_defaults(func=cmd_test)
    args = p.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        p.print_help()


if __name__ == '__main__':
    main()

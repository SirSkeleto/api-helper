from flask import Flask
import twitter
import sys
import sqlite3
import argparse

class StatefulFlask(Flask):
    def __init__(self, name):
        super().__init__(name)
        self.con = sqlite3.connect("master.db", detect_types=sqlite3.PARSE_DECLTYPES)
        self.con.row_factory = sqlite3.Row
        try:
            with self.con:
                self.con.execute("""
                    CREATE TABLE twitter_accounts (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id      INTEGER UNIQUE
                                             NOT NULL,
                        auth_token   TEXT    NOT NULL
                                             CHECK (length(auth_token) == 40),
                        csrf_token   TEXT    NOT NULL
                                             CHECK (length(csrf_token) == 160),
                        bearer_token TEXT    NOT NULL
                                             CHECK (length(bearer_token) == 111 AND 
                                                    bearer_token LIKE "Bearer %") 
                    );
                """)
        except sqlite3.OperationalError:
            pass
        except Exception as e:
            print(e)
        self.state = {}
        self.log_file = open("log.txt", "a")

    def log(s):
        self.log_file.write(s)

    def run(self, host=None, port=None, debug=None, load_dotenv=True, **options):
        if not self.debug or os.getenv('WERKZEUG_RUN_MAIN') == 'true':
            with self.app_context():
                twitter.setup()
        super(StatefulFlask, self).run(host=host, port=port, debug=debug, load_dotenv=load_dotenv, **options)

app = StatefulFlask(__name__)

if __name__ == '__main__':
    con = app.con
    parser = argparse.ArgumentParser(
        prog='Api Helper',
        description='A lightweight flask server that helps finnicky apis (just twitter atm) work nicely with hydrus'
    )
    subparsers = parser.add_subparsers(required=True, dest='command')
    
    parser_run = subparsers.add_parser('run', help='runs the server')
    parser_add = subparsers.add_parser('add', help='adds an account for the api to use')
    parser_list = subparsers.add_parser('list', help='lists accounts for a given service')
    parser_delete = subparsers.add_parser('del', help='removes an account from a given service')
    
    subparsers_add = parser_add.add_subparsers(required=True, dest='service')
    twitter_add = subparsers_add.add_parser('twitter', help='add a twitter account')
    twitter_add.add_argument('id', help='the desired id of the account - the lowest id is always used first')
    twitter_add.add_argument('auth_token', help='the auth_token cookie of the account')
    twitter_add.add_argument('csrf_token', help='the x-csrf-token header of the account')
    twitter_add.add_argument('bearer_token', help='the authorization header of the account')
    
    parser_list.add_argument('service', help='the service for which accounts should be listed')
    
    subparsers_del = parser_delete.add_subparsers(required=True, dest='service')
    twitter_del = subparsers_del.add_parser('twitter', help='remove a twitter account')
    twitter_del.add_argument('id', help='the id of the account to remove')
    
    args = parser.parse_args(sys.argv[1:])
    match args.command:
        case 'run':
            app.run(debug=True)
        case 'add':
            match args.service:
                case 'twitter':
                    try:
                        with con:
                            con.execute("INSERT INTO twitter_accounts VALUES(NULL, ?, ?, ?, ?)", (args.id, args.auth_token, args.csrf_token, args.bearer_token))
                            print("Insert successful.")
                    except Exception as e:
                        print("add failed:")
                        print(e)
        case 'list':
            match args.service:
                case 'twitter':
                    try:
                        with con:
                            res = con.execute("SELECT user_id, auth_token, csrf_token, bearer_token FROM twitter_accounts ORDER BY user_id asc").fetchall()
                            print("Accounts for twitter:")
                            for row in res:
                                print(", ".join(f"{key}: {row[key]}" for key in row.keys()))
                    except Exception as e:
                        print("list failed:")
                        print(e)
        case 'del':
            match args.service:
                case 'twitter':
                    try:
                        with con:
                            con.execute("DELETE FROM twitter_accounts WHERE user_id = ?", (args.id))
                            print("Delete successful.")
                    except Exception as e:
                        print("delete failed:")
                        print(e)